import 'dart:convert';
import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:mime/mime.dart';

import '../config.dart';
import '../models/lor_model.dart';
import '../services/identity.dart';

/// The claimant's document checklist — what the insurer needs for this claim,
/// what has arrived, and what is still outstanding.
///
/// Rows are grouped by outcome rather than listed flat, because "we could not
/// read what you sent" and "you have not sent this" call for different actions
/// from the claimant and must not look the same.
class LorChecklistScreen extends StatefulWidget {
  final String claimId;
  final LorPack pack;

  /// Ruleset slug used to populate the claim-type picker. Falls back to the
  /// pack's own ruleset_id.
  final String? rulesetId;

  const LorChecklistScreen({
    super.key,
    required this.claimId,
    required this.pack,
    this.rulesetId,
  });

  @override
  State<LorChecklistScreen> createState() => _LorChecklistScreenState();
}

class _LorChecklistScreenState extends State<LorChecklistScreen> {
  late LorPack _pack;
  String? _uploadingRequirementId;
  bool _changingClaimType = false;
  
  // Tracks picked files by requirement ID before they are submitted.
  final Map<String, String> _pickedFiles = {};
  bool _uploadedSomething = false;
  bool _polling = false;

  Future<void> _pollLor() async {
    if (_polling) return;
    _polling = true;
    int attempts = 0;
    while (attempts < 40 && mounted && _polling) {
      await Future.delayed(const Duration(seconds: 3));
      if (!mounted) break;
      try {
        final response = await http.get(Uri.parse('$kApiBase/api/claim/${widget.claimId}/lor'));
        if (response.statusCode == 200) {
          final data = jsonDecode(response.body);
          if (data != null && data.isNotEmpty) {
            final newPack = LorPack.tryFrom(data);
            if (newPack != null && mounted) {
              setState(() => _pack = newPack);
            }
          }
        }
      } catch (e) {
        debugPrint('Polling error: $e');
      }
      attempts++;
    }
    _polling = false;
  }

  @override
  void initState() {
    super.initState();
    _pack = widget.pack;
  }

  String get _rulesetSlug =>
      (widget.rulesetId?.isNotEmpty == true) ? widget.rulesetId! : _pack.rulesetId;

  // --- networking -----------------------------------------------------------

  /// Resolves a picked file to a real on-disk path. On Android the picker can
  /// hand back a content:// URI with no usable path, so fall back to writing the
  /// in-memory bytes to a temp file.
  Future<String?> _resolvePath(PlatformFile file) async {
    if (file.path != null && file.path!.isNotEmpty) return file.path;
    if (file.bytes == null) return null;
    final tmp = File(
      '${Directory.systemTemp.path}/${DateTime.now().millisecondsSinceEpoch}_${file.name}',
    );
    await tmp.writeAsBytes(file.bytes!);
    return tmp.path;
  }

  Future<void> _pickFor(RequirementResult req) async {
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['pdf', 'jpg', 'jpeg', 'png'],
    );
    if (result == null || result.files.isEmpty) return;

    final path = await _resolvePath(result.files.first);
    if (path == null) {
      _toast('Could not read that file. Try picking it again.', Colors.amber);
      return;
    }
    
    setState(() {
      _pickedFiles[req.requirementId] = path;
    });
  }

  Future<void> _uploadFor(RequirementResult req) async {
    final path = _pickedFiles[req.requirementId];
    if (path == null) return;

    setState(() => _uploadingRequirementId = req.requirementId);
    try {
      final request = http.MultipartRequest(
        'POST',
        Uri.parse('$kApiBase/api/claim/${widget.claimId}/documents'),
      );
      // Tags the upload with the checklist row it answers.
      request.fields['requirement_id'] = req.requirementId;
      request.headers.addAll(await authHeaders());
      request.files.add(await _multipart(_fieldFor(req), path));

      final response = await request.send().timeout(const Duration(seconds: 120));
      final body = await response.stream.bytesToString();

      if (response.statusCode == 200) {
        if (!mounted) return;
        _uploadedSomething = true;
        setState(() {
          _pickedFiles.remove(req.requirementId);
        });
        _toast(
          'Submitted successfully. We are checking it now — this list will update shortly.',
          const Color(0xFF1E1B4B),
        );
        _pollLor();
      } else {
        _toast(_errorFrom(body, response.statusCode), Colors.redAccent);
      }
    } catch (e) {
      _toast('Upload failed: $e', Colors.redAccent);
    } finally {
      if (mounted) setState(() => _uploadingRequirementId = null);
    }
  }

  /// Route the file under the role field the gateway understands where we can,
  /// so the existing slot checks still apply; anything else goes to the generic
  /// "supporting" slot and is judged by its actual contents instead.
  String _fieldFor(RequirementResult req) {
    final id = req.requirementId.toUpperCase();
    if (id.contains('POLICY')) return 'policy_doc';
    if (id.contains('-ID') || id.contains('KYC')) return 'govt_id';
    if (id.contains('INVOICE') || id.contains('STOCK')) return 'invoices';
    if (id.contains('PHOTO')) return 'photos';
    return 'supporting';
  }

  Future<http.MultipartFile> _multipart(String field, String path) async {
    final mimeStr = lookupMimeType(path) ?? 'application/octet-stream';
    final parts = mimeStr.split('/');
    return http.MultipartFile.fromPath(
      field,
      path,
      contentType:
          MediaType(parts[0], parts.length > 1 ? parts[1] : 'octet-stream'),
    );
  }

  String _errorFrom(String body, int status) {
    try {
      final decoded = jsonDecode(body);
      if (decoded is Map && decoded['detail'] != null) {
        return decoded['detail'].toString();
      }
      if (decoded is Map && decoded['error'] != null) {
        return decoded['error'].toString();
      }
    } catch (_) {}
    return 'Upload failed ($status).';
  }

  void _toast(String message, Color colour) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: colour,
        duration: const Duration(seconds: 4),
      ),
    );
  }

  // --- claim-type correction ------------------------------------------------

  Future<void> _changeClaimType() async {
    setState(() => _changingClaimType = true);
    List<ClaimTypeOption> options = [];
    try {
      final response = await http
          .get(Uri.parse('$kApiBase/api/requirements/$_rulesetSlug'))
          .timeout(const Duration(seconds: 20));
      if (response.statusCode == 200) {
        final decoded = jsonDecode(response.body);
        final data = decoded is Map && decoded['data'] != null
            ? decoded['data']
            : decoded;
        options = ((data['claim_types'] as List?) ?? const [])
            .map((e) => ClaimTypeOption.fromJson(Map<String, dynamic>.from(e)))
            .toList();
      }
    } catch (_) {
      // fall through to the empty-list message below
    } finally {
      if (mounted) setState(() => _changingClaimType = false);
    }

    if (!mounted) return;
    if (options.isEmpty) {
      _toast('Could not load the list of claim types.', Colors.amber);
      return;
    }

    final chosen = await showModalBottomSheet<ClaimTypeOption>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(20, 20, 20, 8),
              child: Text(
                'What kind of claim is this?',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
            ),
            const Padding(
              padding: EdgeInsets.fromLTRB(20, 0, 20, 12),
              child: Text(
                'Picking the right one changes which documents you are asked for.',
                style: TextStyle(color: Color(0xFF64748B)),
              ),
            ),
            for (final option in options)
              ListTile(
                title: Text(option.label),
                subtitle: option.description.isEmpty
                    ? null
                    : Text(option.description,
                        style: const TextStyle(fontSize: 12)),
                trailing: option.id == _pack.claimType
                    ? const Icon(Icons.check, color: Color(0xFF16A34A))
                    : null,
                onTap: () => Navigator.pop(ctx, option),
              ),
            const SizedBox(height: 12),
          ],
        ),
      ),
    );
    if (chosen == null || chosen.id == _pack.claimType) return;

    try {
      final response = await http
          .post(
            Uri.parse('$kApiBase/api/claim/${widget.claimId}/claim-type'),
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode({'claim_type_id': chosen.id}),
          )
          .timeout(const Duration(seconds: 30));
      if (response.statusCode == 200) {
        final decoded = jsonDecode(response.body);
        final data =
            decoded is Map && decoded['data'] != null ? decoded['data'] : decoded;
        final updated = LorPack.tryFrom(data);
        if (updated != null && mounted) {
          setState(() => _pack = updated);
          _toast('Checklist updated for ${chosen.label}.', const Color(0xFF1E1B4B));
        }
      } else {
        _toast(_errorFrom(response.body, response.statusCode), Colors.redAccent);
      }
    } catch (e) {
      _toast('Could not update the claim type: $e', Colors.redAccent);
    }
  }

  // --- UI -------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    final outstandingBlocking =
        _pack.missing.where((r) => r.isBlocking).toList();
    final outstandingAdvisory =
        _pack.missing.where((r) => !r.isBlocking).toList();

    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, dynamic result) {
        if (didPop) return;
        Navigator.pop(context, _uploadedSomething);
      },
      child: Scaffold(
        backgroundColor: const Color(0xFFF8FAFC),
      appBar: AppBar(
        title: const Text('Documents needed'),
        backgroundColor: Colors.white,
        elevation: 0.5,
        foregroundColor: const Color(0xFF0F172A),
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            _header(),
            if (_pack.notes.isNotEmpty) ...[
              const SizedBox(height: 12),
              for (final note in _pack.notes) _noteBanner(note),
            ],
            const SizedBox(height: 16),
            if (outstandingBlocking.isNotEmpty)
              _section(
                'Still needed',
                'Your claim cannot be assessed until these arrive.',
                Icons.error_outline,
                const Color(0xFFDC2626),
                outstandingBlocking,
              ),
            if (_pack.unverified.isNotEmpty)
              _section(
                "Couldn't be read",
                'These arrived but we could not read them. A clearer copy would help.',
                Icons.help_outline,
                const Color(0xFFD97706),
                _pack.unverified,
              ),
            if (outstandingAdvisory.isNotEmpty)
              _section(
                'Helpful to add',
                'Not holding anything up, but your insurer will want these.',
                Icons.add_circle_outline,
                const Color(0xFF2563EB),
                outstandingAdvisory,
              ),
            if (_pack.satisfied.isNotEmpty)
              _section(
                'Received',
                null,
                Icons.check_circle_outline,
                const Color(0xFF16A34A),
                _pack.satisfied,
              ),
            const SizedBox(height: 24),
          ],
        ),
      ),
    ));
  }

  Widget _header() {
    final done = _pack.satisfied.length;
    final total = _pack.totalCount;
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: const Color(0xFFE2E8F0)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            total == 0
                ? 'No document checklist is available for this claim.'
                : '$done of $total documents received',
            style: const TextStyle(fontSize: 17, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 6),
          Text(
            _pack.isProvisional
                // Revision 1 predates claim-type classification, so it is
                // genuinely incomplete. Say so rather than implying it is final.
                ? 'We are still working out what kind of claim this is. More '
                    'documents may be added to this list in a moment.'
                : 'This list is based on what your insurer requires for this '
                    'kind of claim.',
            style: const TextStyle(color: Color(0xFF64748B), fontSize: 13),
          ),
          if (_pack.claimTypeLabel != null) ...[
            const SizedBox(height: 12),
            Row(
              children: [
                const Icon(Icons.label_outline, size: 18, color: Color(0xFF64748B)),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    'Treated as: ${_pack.claimTypeLabel}',
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton(
                onPressed: _changingClaimType ? null : _changeClaimType,
                style: TextButton.styleFrom(padding: EdgeInsets.zero),
                child: Text(_changingClaimType
                    ? 'Loading…'
                    : "Not the right kind of claim?"),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _noteBanner(String note) {
    return Container(
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0xFFFEF3C7),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0xFFFDE68A)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.info_outline, size: 18, color: Color(0xFF92400E)),
          const SizedBox(width: 8),
          Expanded(
            child: Text(note,
                style: const TextStyle(fontSize: 13, color: Color(0xFF92400E))),
          ),
        ],
      ),
    );
  }

  Widget _section(
    String title,
    String? subtitle,
    IconData icon,
    Color colour,
    List<RequirementResult> rows,
  ) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(4, 16, 4, 8),
          child: Row(
            children: [
              Icon(icon, size: 20, color: colour),
              const SizedBox(width: 8),
              Text(
                '$title (${rows.length})',
                style: TextStyle(
                    fontSize: 15, fontWeight: FontWeight.bold, color: colour),
              ),
            ],
          ),
        ),
        if (subtitle != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(4, 0, 4, 8),
            child: Text(subtitle,
                style: const TextStyle(fontSize: 12, color: Color(0xFF64748B))),
          ),
        for (final row in rows) _row(row, colour),
      ],
    );
  }

  Widget _row(RequirementResult req, Color colour) {
    final isDone = req.status == RequirementStatus.satisfied;
    final busy = _uploadingRequirementId == req.requirementId;

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: const Color(0xFFE2E8F0)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(
                isDone ? Icons.check_circle : Icons.radio_button_unchecked,
                size: 20,
                color: isDone ? const Color(0xFF16A34A) : colour,
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(req.label,
                        style: const TextStyle(
                            fontWeight: FontWeight.w600, fontSize: 15)),
                    if (req.message.isNotEmpty) ...[
                      const SizedBox(height: 4),
                      Text(req.message,
                          style: const TextStyle(
                              fontSize: 12.5, color: Color(0xFF475569))),
                    ],
                  ],
                ),
              ),
              if (req.isBlocking && !isDone)
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: const Color(0xFFFEE2E2),
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: const Text('Required',
                      style: TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.w600,
                          color: Color(0xFFB91C1C))),
                ),
            ],
          ),
          // An attested row was ticked because a file arrived for it, not
          // because anyone checked what it was. Never let it read as verified.
          if (req.isUnverifiedTick)
            const Padding(
              padding: EdgeInsets.only(left: 30, top: 6),
              child: Text(
                'Received — contents not verified',
                style: TextStyle(fontSize: 11.5, color: Color(0xFF64748B)),
              ),
            ),
          if (!isDone) ...[
            const SizedBox(height: 10),
            if (_pickedFiles.containsKey(req.requirementId)) ...[
              Row(
                children: [
                  const Icon(Icons.insert_drive_file, size: 16, color: Colors.grey),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(
                      _pickedFiles[req.requirementId]!.split('/').last,
                      style: const TextStyle(fontSize: 12, color: Colors.black87),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.close, size: 16, color: Colors.grey),
                    onPressed: busy ? null : () {
                      setState(() {
                        _pickedFiles.remove(req.requirementId);
                      });
                    },
                    padding: EdgeInsets.zero,
                    constraints: const BoxConstraints(),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton.icon(
                  onPressed: busy ? null : () => _uploadFor(req),
                  icon: busy
                      ? const SizedBox(
                          width: 14,
                          height: 14,
                          child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : const Icon(Icons.send, size: 16, color: Colors.white),
                  label: Text(busy ? 'Submitting...' : 'Submit Document', style: const TextStyle(color: Colors.white)),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF1E1B4B),
                  ),
                ),
              ),
            ] else ...[
              Align(
                alignment: Alignment.centerLeft,
                child: OutlinedButton.icon(
                  onPressed: () => _pickFor(req),
                  icon: const Icon(Icons.upload_file, size: 18),
                  label: Text(req.status == RequirementStatus.unverified
                      ? 'Upload a clearer copy'
                      : 'Upload'),
                ),
              ),
            ],
          ],
        ],
      ),
    );
  }
}

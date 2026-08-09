import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:intl/intl.dart';
import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:flutter_markdown/flutter_markdown.dart';
import '../config.dart';
import '../models/claim_model.dart';
import '../models/lor_model.dart';
import '../services/identity.dart';
import '../services/draft_store.dart';
import 'claim_form_screen.dart';
import 'lor_checklist_screen.dart';
import 'upload_policy_screen.dart';

class ItemListScreen extends StatefulWidget {
  const ItemListScreen({super.key});

  @override
  State<ItemListScreen> createState() => _ItemListScreenState();
}

class _ItemListScreenState extends State<ItemListScreen> {
  List<ClaimRecord> _claims = [];
  List<DraftClaim> _drafts = [];
  String _selectedFilter = 'All';
  String _businessName = 'My Claims Dashboard';
  String _policyNumber = 'POL-ACTIVE';
  String _insurerName = 'Insurance Company';
  String? _userCategory;

  @override
  void initState() {
    super.initState();
    _loadDashboardData();
  }

  Future<void> _loadDashboardData() async {
    final prefs = await SharedPreferences.getInstance();
    final category = prefs.getString('user_category') ?? 'Personal';
    final businessName = prefs.getString('policy_business_name') ?? 'My Claims Dashboard';

    final demo = <ClaimRecord>[];
    if (category == 'Insurance Firm' && businessName.contains('ABC')) {
      demo.addAll([
        ClaimRecord(
          id: 'CLM-ABC-001',
          itemName: 'Damaged Warehouse Roof',
          category: 'Commercial',
          itemType: 'Commercial Property & Building',
          geotag: '28.7041° N, 77.1025° E',
          timestamp: '2026-08-08 14:20:00',
          permanentAddress: 'Delhi Industrial Area',
          lossDate: DateTime.now().subtract(const Duration(days: 2)),
          status: ClaimStatus.pending,
          businessType: 'Logistics',
        ),
        ClaimRecord(
          id: 'CLM-ABC-002',
          itemName: 'Flooded Server Room',
          category: 'Commercial',
          itemType: 'IT Hardware & Office Electronics',
          geotag: '12.9716° N, 77.5946° E',
          timestamp: '2026-08-07 09:15:00',
          permanentAddress: 'Bengaluru Tech Park',
          lossDate: DateTime.now().subtract(const Duration(days: 4)),
          status: ClaimStatus.review,
          businessType: 'IT Services',
          draftPackSummary: '### Main Schedule\nThe server room equipment is covered under comprehensive peril.\n\n### Rejected Items\nNo items rejected.',
        ),
      ]);
    }

    setState(() {
      _userCategory = category;
      _businessName = businessName;
      _policyNumber = prefs.getString('policy_number') ?? 'POL-ACTIVE';
      _insurerName = prefs.getString('policy_insurer') ?? 'Insurance Company';
      _claims = demo;
    });

    // Offline drafts saved on this device (captured with no internet).
    await _reloadDrafts();

    // Claim history from the server — this is what makes claims survive an app
    // restart / reinstall instead of living only in memory.
    await _fetchServerClaims();
  }

  Future<void> _reloadDrafts() async {
    final drafts = await DraftStore.load();
    if (!mounted) return;
    setState(() => _drafts = drafts);
  }

  /// Resume an offline draft: reopen the claim form pre-filled with the captured
  /// evidence and any entered details. On a successful submit the form clears the
  /// draft; either way we refresh the drafts list.
  Future<void> _resumeDraft(DraftClaim d) async {
    List<Map<String, dynamic>>? confirmed;
    if (d.confirmedItemsJson != null && d.confirmedItemsJson!.isNotEmpty) {
      try {
        confirmed = (jsonDecode(d.confirmedItemsJson!) as List)
            .map((e) => Map<String, dynamic>.from(e as Map))
            .toList();
      } catch (_) {
        confirmed = null;
      }
    }
    final result = await Navigator.push<ClaimRecord>(
      context,
      MaterialPageRoute(
        builder: (_) => ClaimFormScreen(
          draftId: d.id,
          policyPdfName: d.policyPdfName,
          policyPdfPath: d.policyPdfPath,
          photoPath: d.photoPath,
          geotag: d.geotag,
          timestamp: d.timestamp,
          photoLat: d.photoLat,
          photoLon: d.photoLon,
          photoCapturedAt: d.photoCapturedAt,
          userCategory: d.userCategory,
          confirmedItems: confirmed,
          initialItemName: d.itemName,
          initialItemType: d.itemType,
          initialAddress: d.permanentAddress,
          initialBusinessType: d.businessType,
          initialGstin: d.gstinNumber,
          initialLossDate: d.lossDate != null ? DateTime.tryParse(d.lossDate!) : null,
          initialGovtIdName: d.govtIdName,
          initialGovtIdPath: d.govtIdPath,
          initialInvoiceName: d.invoiceName,
          initialInvoicePath: d.invoicePath,
        ),
      ),
    );
    await _reloadDrafts();
    if (result != null && mounted) {
      setState(() => _claims.insert(0, result));
      if (result.id.startsWith('CLM-') && result.id.length > 10) {
        _pollClaimStatus(result.id);
      }
    }
  }

  Future<void> _deleteDraft(DraftClaim d) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Discard draft?'),
        content: const Text('This removes the saved photo and details for this draft.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Discard', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok == true) {
      await DraftStore.delete(d.id);
      await _reloadDrafts();
    }
  }

  /// GET /api/claims (scoped to the signed-in user via the bearer token) and
  /// merge any claims not already shown. Best effort — offline just shows the
  /// local/demo list.
  Future<void> _fetchServerClaims() async {
    try {
      final headers = await authHeaders();
      final resp = await http
          .get(Uri.parse('$kApiBase/api/claims?limit=50'), headers: headers)
          .timeout(const Duration(seconds: 20));
      if (resp.statusCode != 200) return;
      final data = jsonDecode(resp.body);
      final list = (data['claims'] as List?) ?? [];
      final serverClaims = [
        for (final c in list) if (c is Map) _serverClaimToRecord(c),
      ];
      if (!mounted) return;
      setState(() {
        final existing = _claims.map((e) => e.id).toSet();
        for (final sc in serverClaims) {
          if (sc.id.isNotEmpty && !existing.contains(sc.id)) _claims.add(sc);
        }
      });
    } catch (e) {
      debugPrint('Fetch claims skipped (offline?): $e');
    }
  }

  ClaimStatus _mapServerStatus(String s) {
    switch (s) {
      case 'completed':
        return ClaimStatus.review;
      case 'awaiting_documents':
        return ClaimStatus.awaitingDocuments;
      case 'failed':
        return ClaimStatus.review;
      default:
        return ClaimStatus.pending;
    }
  }

  ClaimRecord _serverClaimToRecord(Map c) {
    DateTime loss;
    try {
      loss = DateTime.parse((c['created_at'] ?? '').toString());
    } catch (_) {
      loss = DateTime.now();
    }
    final desc = (c['event_description'] ?? '').toString();
    return ClaimRecord(
      id: (c['claim_ref'] ?? '').toString(),
      itemName: desc.isNotEmpty ? desc : 'Claim ${c['claim_ref']}',
      category: _userCategory ?? 'Personal',
      itemType: (c['claim_type'] ?? '—').toString(),
      geotag: 'Location on file',
      timestamp: (c['created_at'] ?? '').toString(),
      permanentAddress: '',
      lossDate: loss,
      status: _mapServerStatus((c['status'] ?? '').toString()),
    );
  }

  /// A claim from the list has only a summary. Fetch its full state on demand so
  /// the review modal / checklist have the draft pack and LOR.
  Future<ClaimRecord?> _hydrateClaim(ClaimRecord claim) async {
    try {
      final resp = await http
          .get(Uri.parse('$kApiBase/api/claim/${claim.id}'))
          .timeout(const Duration(seconds: 20));
      if (resp.statusCode != 200) return null;
      final data = jsonDecode(resp.body);
      final state = data['state'] ?? {};
      final draftPack = state['draft_pack'] ?? {};
      final updated = claim.copyWith(
        draftPackSummary: _buildDraftSummary(draftPack, state),
        policyStatusText: _summarizePolicyStatus(state),
        lor: LorPack.tryFrom(state['lor']),
      );
      final idx = _claims.indexWhere((e) => e.id == claim.id);
      if (idx != -1 && mounted) setState(() => _claims[idx] = updated);
      return updated;
    } catch (e) {
      debugPrint('Hydrate failed: $e');
      return null;
    }
  }

  Future<void> _onClaimTap(ClaimRecord claim) async {
    var c = claim;
    final needsHydration =
        (c.status == ClaimStatus.review && c.draftPackSummary == null) ||
            (c.status == ClaimStatus.awaitingDocuments && c.lor == null);
    if (needsHydration) {
      final hydrated = await _hydrateClaim(c);
      if (hydrated != null) c = hydrated;
    }
    if (!mounted) return;
    if (c.status == ClaimStatus.awaitingDocuments) {
      _openChecklist(c);
    } else if (c.status == ClaimStatus.review) {
      _showDraftPackReviewModal(c);
    } else if (c.status == ClaimStatus.pending) {
      _showPendingStatusModal(c);
    } else {
      _showDraftPackReviewModal(c);
    }
  }

  Future<void> _signOut() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('is_signed_in', false);

    if (!mounted) return;
    Navigator.of(context).pushNamedAndRemoveUntil('/', (route) => false);
  }

  void _startNewClaimFlow() async {
    final newClaim = await Navigator.push<ClaimRecord>(
      context,
      MaterialPageRoute(
        builder: (_) => UploadPolicyScreen(userCategory: _userCategory),
      ),
    );

    // The user may have saved a draft partway through (e.g. offline).
    await _reloadDrafts();

    if (newClaim != null) {
      setState(() {
        _claims.insert(0, newClaim);
      });

      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Row(
            children: [
              const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(strokeWidth: 2, color: Colors.amberAccent),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text('Claim "${newClaim.itemName}" submitted! Status: Pending (LangChain AI Processing...)'),
              ),
            ],
          ),
          backgroundColor: const Color(0xFF0F172A),
          duration: const Duration(seconds: 4),
        ),
      );

      if (newClaim.id.startsWith('CLM-') && newClaim.id.length > 10) {
        _pollClaimStatus(newClaim.id);
      }
    }
  }

  /// Derives a human status from the pipeline's final state instead of showing
  /// a hardcoded "COVERED" badge. Returns one of: COVERED, NEEDS REVIEW,
  /// NOT COVERED, NO ITEMS.
  String _summarizePolicyStatus(Map<String, dynamic> state) {
    // A hard intake rejection (menu-as-policy, swapped files, out-of-period
    // loss) leaves line_items and rejected_items empty, which would otherwise
    // fall through to a benign-looking "NO ITEMS". Catch it first.
    if (state['intake_ok'] == false) return 'REJECTED';
    final lineItems = (state['line_items'] as List?) ?? [];
    final rejected = (state['rejected_items'] as List?) ?? [];
    int covered = 0, review = 0, excluded = 0;
    for (final item in lineItems) {
      final status = (item is Map) ? item['policy_status'] : null;
      if (status == 'covered') covered++;
      if (status == 'review') review++;
      if (status == 'excluded') excluded++;
    }
    // Anything needing a human eye (a covered item alongside excluded/rejected
    // ones, or an explicit review) is "NEEDS REVIEW".
    if (review > 0) return 'NEEDS REVIEW';
    if (covered > 0 && (excluded > 0 || rejected.isNotEmpty)) return 'NEEDS REVIEW';
    if (covered > 0) return 'COVERED';
    // No covered items, but there ARE excluded/rejected ones -> not covered.
    if (excluded > 0 || rejected.isNotEmpty) return 'NOT COVERED';
    return 'NO ITEMS';
  }

  Widget _buildStatusBadge(String? statusText) {
    final status = (statusText ?? '').toUpperCase();
    Color bg, border, fg, sub;
    IconData icon;
    String title, subtitle;

    // Order matters: REJECTED and NOT COVERED both need to precede COVERED
    // (which they'd substring-match).
    if (status.contains('REJECTED')) {
      bg = const Color(0xFFFEF2F2);
      border = const Color(0xFFEF4444);
      fg = const Color(0xFF991B1B);
      sub = const Color(0xFFB91C1C);
      icon = Icons.block;
      title = 'CLAIM NOT ACCEPTED';
      subtitle = 'Your documents could not be verified — see below.';
    } else if (status.contains('NOT COVERED')) {
      bg = const Color(0xFFFEF2F2);
      border = const Color(0xFFEF4444);
      fg = const Color(0xFF991B1B);
      sub = const Color(0xFFB91C1C);
      icon = Icons.cancel_outlined;
      title = 'Policy Status: NOT COVERED';
      subtitle = 'No items were covered — see the excluded / rejected annexures below.';
    } else if (status.contains('REVIEW')) {
      bg = const Color(0xFFFFFBEB);
      border = const Color(0xFFF59E0B);
      fg = const Color(0xFF92400E);
      sub = const Color(0xFFB45309);
      icon = Icons.warning_amber_rounded;
      title = 'Policy Status: NEEDS REVIEW';
      subtitle = 'Some items need manual review, are excluded, or were rejected.';
    } else if (status.contains('COVERED')) {
      bg = const Color(0xFFECFDF5);
      border = const Color(0xFF10B981);
      fg = const Color(0xFF065F46);
      sub = const Color(0xFF047857);
      icon = Icons.check_circle;
      title = 'Policy Status: COVERED';
      subtitle = 'All identified items are covered under the policy.';
    } else if (status.contains('ERROR')) {
      bg = const Color(0xFFFEF2F2);
      border = const Color(0xFFEF4444);
      fg = const Color(0xFF991B1B);
      sub = const Color(0xFFB91C1C);
      icon = Icons.error_outline;
      title = 'Processing Error';
      subtitle = 'The AI pipeline could not complete this claim.';
    } else {
      bg = const Color(0xFFF1F5F9);
      border = const Color(0xFF94A3B8);
      fg = const Color(0xFF334155);
      sub = const Color(0xFF64748B);
      icon = Icons.info_outline;
      title = 'Policy Status: NO ITEMS';
      subtitle = 'No claimable items were identified in the photo.';
    }

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: border),
      ),
      child: Row(
        children: [
          Icon(icon, color: border, size: 22),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: fg),
                ),
                Text(subtitle, style: TextStyle(fontSize: 12, color: sub)),
              ],
            ),
          ),
        ],
      ),
    );
  }

  /// Renders every annexure so no item can silently vanish. An item the policy
  /// EXCLUDES is not "rejected" and is not in the main schedule — it lives in
  /// its own excluded annexure, which must be shown to avoid false negatives.
  String _buildDraftSummary(Map draftPack, Map state) {
    String section(String title, dynamic body) {
      final text = (body == null || body.toString().trim().isEmpty) ? 'None.' : body.toString().trim();
      return '### $title\n$text';
    }

    // Hard rejection path: lead with WHY, and what each uploaded file looked
    // like. The reason strings are already user-facing sentences.
    if (state['intake_ok'] == false) {
      final parts = <String>['CLAIM NOT ACCEPTED'];
      final reasons = (state['intake_reasons'] as List?) ?? [];
      if (reasons.isNotEmpty) {
        parts.add('### Why this claim was not accepted\n${reasons.map((r) => '- $r').join('\n')}');
      }
      final docs = (state['documents'] as List?) ?? [];
      final mismatched = docs.where((d) => d is Map && d['classification_verdict'] == 'mismatch').toList();
      if (mismatched.isNotEmpty) {
        parts.add('### Documents that did not match\n${mismatched.map((d) {
          final kind = (d['classification_kind'] ?? 'unidentified').toString().replaceAll('_', ' ');
          return '- ${d['document_type']}: looks like $kind';
        }).join('\n')}');
      }
      return parts.join('\n\n');
    }

    final parts = <String>[
      'DRAFT PACK GENERATED:',
      section('Main Schedule (Covered / Under Review)', draftPack['main_schedule']),
      section('Excluded by Policy', draftPack['excluded_items_annexure']),
      section('Rejected Items', draftPack['rejected_items_annexure']),
      section('Pending Verification', draftPack['pending_verification_annexure']),
    ];

    final warnings = (state['warnings'] as List?) ?? [];
    if (warnings.isNotEmpty) {
      parts.add('### Warnings\n${warnings.map((w) => '- $w').join('\n')}');
    }
    return parts.join('\n\n');
  }

  Future<void> _pollClaimStatus(String claimId) async {
    const int maxAttempts = 40;
    int attempts = 0;

    while (attempts < maxAttempts) {
      await Future.delayed(const Duration(seconds: 3));
      if (!mounted) return;

      try {
        final response = await http.get(Uri.parse('$kApiBase/api/claim/$claimId'));
        if (response.statusCode == 200) {
          final data = jsonDecode(response.body);
          if (data['status'] == 'completed') {
            final state = data['state'] ?? {};
            final draftPack = state['draft_pack'] ?? {};
            final statusText = _summarizePolicyStatus(state);
            final index = _claims.indexWhere((c) => c.id == claimId);
            // Accept any non-review state: after a re-upload the claim resumes
            // from awaitingDocuments, not from pending.
            if (index != -1 && _claims[index].status != ClaimStatus.review) {
              setState(() {
                _claims[index] = _claims[index].copyWith(
                  status: ClaimStatus.review,
                  draftPackSummary: _buildDraftSummary(draftPack, state),
                  aiReasoning: 'AI completed analysis and generated the draft pack.',
                  policyStatusText: statusText,
                  lor: LorPack.tryFrom(state['lor']),
                );
              });
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(
                  content: Row(
                    children: [
                      const Icon(Icons.rate_review, color: Colors.blueAccent),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Text('LangChain Draft Pack generated! Claim is ready for Review.'),
                      ),
                    ],
                  ),
                  backgroundColor: const Color(0xFF1E1B4B),
                  duration: const Duration(seconds: 5),
                ),
              );
            }
            return;
          } else if (data['status'] == 'awaiting_documents') {
            // Terminal for THIS run, but not a failure and not a rejection —
            // the claim resumes once the outstanding documents arrive. Must
            // stop polling here, or the loop burns all 40 attempts and reports
            // a timeout for a claim that is simply waiting on the user.
            final state = data['state'] ?? {};
            final pack = LorPack.tryFrom(state['lor']);
            final index = _claims.indexWhere((c) => c.id == claimId);
            if (index != -1) {
              setState(() {
                _claims[index] = _claims[index].copyWith(
                  status: ClaimStatus.awaitingDocuments,
                  lor: pack,
                  policyStatusText: 'DOCUMENTS NEEDED',
                  aiReasoning:
                      'Some documents your insurer requires for this kind of '
                      'claim are still outstanding.',
                );
              });
              final count = pack?.blockingMissing.length ?? 0;
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(
                  content: Text(count > 0
                      ? '$count more document${count == 1 ? '' : 's'} needed — tap the claim to see the checklist.'
                      : 'More documents are needed — tap the claim to see the checklist.'),
                  backgroundColor: const Color(0xFFD97706),
                  duration: const Duration(seconds: 5),
                ),
              );
            }
            return;
          } else if (data['status'] == 'failed') {
            debugPrint('Pipeline failed: ${data['error']}');
            final index = _claims.indexWhere((c) => c.id == claimId);
            if (index != -1 && _claims[index].status != ClaimStatus.review) {
              setState(() {
                _claims[index] = _claims[index].copyWith(
                  status: ClaimStatus.review, // Or keep pending but show error
                  aiReasoning: 'AI Pipeline Failed: ${data['error']}',
                  policyStatusText: 'Error',
                  draftPackSummary: 'The AI pipeline encountered an error and could not generate a draft pack.\n\nError: ${data['error']}'
                );
              });
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(
                  content: Text('AI Pipeline Failed: ${data['error']}'),
                  backgroundColor: Colors.redAccent,
                  duration: const Duration(seconds: 5),
                ),
              );
            }
            return;
          }
        }
      } catch (e) {
        debugPrint('Polling error: $e');
      }
      attempts++;
    }
  }

  /// Opens the document checklist. When the user uploads something the screen
  /// pops with `true`, meaning the pipeline is running again — so resume polling.
  Future<void> _openChecklist(ClaimRecord claim) async {
    final pack = claim.lor;
    if (pack == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('No document checklist is available for this claim yet.'),
          backgroundColor: Colors.amber,
        ),
      );
      return;
    }
    final resumed = await Navigator.push<bool>(
      context,
      MaterialPageRoute(
        builder: (_) => LorChecklistScreen(claimId: claim.id, pack: pack),
      ),
    );
    if (resumed == true && mounted) {
      final index = _claims.indexWhere((c) => c.id == claim.id);
      if (index != -1) {
        setState(() {
          _claims[index] = _claims[index].copyWith(status: ClaimStatus.pending);
        });
      }
      _pollClaimStatus(claim.id);
    }
  }

  List<ClaimRecord> get _filteredClaims {
    if (_selectedFilter == 'All') return _claims;
    if (_selectedFilter == 'Submitted') {
      return _claims.where((c) => c.status == ClaimStatus.submitted).toList();
    }
    if (_selectedFilter == 'Pending') {
      // A claim waiting on documents is still outstanding work for the user, so
      // it belongs in the same bucket rather than disappearing from every filter.
      return _claims
          .where((c) =>
              c.status == ClaimStatus.pending ||
              c.status == ClaimStatus.awaitingDocuments)
          .toList();
    }
    if (_selectedFilter == 'Review') {
      return _claims.where((c) => c.status == ClaimStatus.review).toList();
    }
    return _claims;
  }

  void _showDraftPackReviewModal(ClaimRecord claim) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) {
        return Container(
          height: MediaQuery.of(context).size.height * 0.85,
          decoration: const BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
          ),
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Header Drag Indicator & Title
              Center(
                child: Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(
                    color: Colors.grey.shade300,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              const SizedBox(height: 16),

              Row(
                children: [
                  Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: const Color(0xFFEEF2FF),
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: const Icon(Icons.auto_awesome, color: Color(0xFF4F46E5), size: 24),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'LangChain Draft Pack Review',
                          style: const TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.bold,
                            color: Color(0xFF0F172A),
                          ),
                        ),
                        Text(
                          'Claim ID: ${claim.id}',
                          style: const TextStyle(fontSize: 12, color: Colors.grey),
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.close),
                    onPressed: () => Navigator.pop(context),
                  ),
                ],
              ),
              const SizedBox(height: 16),

              // Coverage Status Badge — driven by the pipeline's actual result.
              _buildStatusBadge(claim.policyStatusText),
              const SizedBox(height: 16),

              // Draft Pack Generated Markdown Box
              const Text(
                'DRAFT PACK OUTPUT',
                style: TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.bold,
                  color: Colors.grey,
                  letterSpacing: 1.0,
                ),
              ),
              const SizedBox(height: 8),

              Expanded(
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: const Color(0xFFF8FAFC),
                    borderRadius: BorderRadius.circular(14),
                    border: Border.all(color: Colors.grey.shade300),
                  ),
                  child: SingleChildScrollView(
                    child: MarkdownBody(
                      data: claim.draftPackSummary ??
                          '''### Main Schedule of Claimed Items
The following items have been reviewed and are confirmed as covered:

1. **${claim.itemName}**
   - **Category:** ${claim.itemType}
   - **Policy Status:** Covered
   - **Evidence:** Geotagged Photo (${claim.geotag})
   - **Reasoning:** Item damage verified against insurance coverage policies.

### REJECTED ITEMS:
There are currently no rejected items.''',
                      styleSheet: MarkdownStyleSheet(
                        p: const TextStyle(fontSize: 14, color: Color(0xFF1E293B)),
                        h3: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Color(0xFF0F172A)),
                      ),
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 20),

              // Accept & Confirm Button (Transitions status to Submitted)
              SizedBox(
                width: double.infinity,
                height: 52,
                child: ElevatedButton.icon(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF059669), // Green Accept Button
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(14),
                    ),
                  ),
                  onPressed: () {
                    setState(() {
                      final index = _claims.indexWhere((c) => c.id == claim.id);
                      if (index != -1) {
                        _claims[index] = _claims[index].copyWith(status: ClaimStatus.submitted);
                      }
                    });
                    Navigator.pop(context);
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(
                        content: Row(
                          children: [
                            const Icon(Icons.verified, color: Colors.white),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Text('Claim "${claim.itemName}" accepted! Status changed to Submitted.'),
                            ),
                          ],
                        ),
                        backgroundColor: const Color(0xFF059669),
                        duration: const Duration(seconds: 4),
                      ),
                    );
                  },
                  icon: const Icon(Icons.check_circle_outline, color: Colors.white, size: 22),
                  label: const Text(
                    'Accept & Confirm Claim',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  void _showPendingStatusModal(ClaimRecord claim) {
    showDialog(
      context: context,
      builder: (context) {
        return AlertDialog(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          title: Row(
            children: [
              const SizedBox(
                width: 20,
                height: 20,
                child: CircularProgressIndicator(strokeWidth: 2.5, color: Color(0xFF4F46E5)),
              ),
              const SizedBox(width: 12),
              const Expanded(
                child: Text('LangChain AI Processing', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
              ),
            ],
          ),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Claim ID: ${claim.id}', style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13)),
              const SizedBox(height: 8),
              Text(
                'LangChain vision agent is analyzing evidence image and verifying policy coverage rules for "${claim.itemName}".',
                style: const TextStyle(fontSize: 13, color: Colors.black87),
              ),
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: const Color(0xFFFFFBEB),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: const Color(0xFFF59E0B)),
                ),
                child: const Row(
                  children: [
                    Icon(Icons.hourglass_bottom, color: Color(0xFFD97706), size: 18),
                    SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'Status: Pending. Will transition to Review automatically once complete.',
                        style: TextStyle(fontSize: 11, color: Color(0xFF92400E)),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Close'),
            ),
          ],
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF8FAFC),
      appBar: AppBar(
        backgroundColor: Colors.white,
        elevation: 0.5,
        title: Row(
          children: [
            Image.asset(
              'assets/logo.png',
              height: 36,
              fit: BoxFit.contain,
              errorBuilder: (context, error, stackTrace) =>
                  Image.asset('assets/placeholder.png', height: 36),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    _businessName,
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                      color: Color(0xFF0F172A),
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                  Row(
                    children: [
                      Container(
                        width: 8,
                        height: 8,
                        decoration: const BoxDecoration(
                          color: Colors.green,
                          shape: BoxShape.circle,
                        ),
                      ),
                      const SizedBox(width: 4),
                      Expanded(
                        child: Text(
                          '$_insurerName • $_policyNumber',
                          style: const TextStyle(fontSize: 11, color: Colors.grey),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ],
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.logout, color: Colors.grey),
            tooltip: 'Sign Out',
            onPressed: _signOut,
          ),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: [
            // Status Header Bar
            Container(
              margin: const EdgeInsets.all(16),
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                gradient: const LinearGradient(
                  colors: [Color(0xFF1E293B), Color(0xFF334155)],
                ),
                borderRadius: BorderRadius.circular(16),
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withOpacity(0.08),
                    blurRadius: 10,
                    offset: const Offset(0, 4),
                  ),
                ],
              ),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        'Active Claims Overview',
                        style: TextStyle(color: Colors.white70, fontSize: 12),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        '${_claims.length} Claims Filed',
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 20,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ],
                  ),
                  ElevatedButton.icon(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: const Color(0xFF4F46E5),
                      foregroundColor: Colors.white,
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(20),
                      ),
                    ),
                    onPressed: _startNewClaimFlow,
                    icon: const Icon(Icons.add, size: 18),
                    label: const Text('File Claim', style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
                  ),
                ],
              ),
            ),

            // Filter Chips Bar (Submitted / Pending / Review)
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Row(
                children: [
                  _buildFilterChip('All', _claims.length),
                  _buildFilterChip(
                      'Submitted',
                      _claims
                          .where((c) => c.status == ClaimStatus.submitted)
                          .length),
                  _buildFilterChip(
                      'Pending',
                      _claims
                          .where((c) =>
                              c.status == ClaimStatus.pending ||
                              c.status == ClaimStatus.awaitingDocuments)
                          .length),
                  _buildFilterChip(
                      'Review',
                      _claims
                          .where((c) => c.status == ClaimStatus.review)
                          .length),
                ],
              ),
            ),
            const SizedBox(height: 8),

            // Claims List or Empty State
            Expanded(
              child: (_drafts.isEmpty && _filteredClaims.isEmpty)
                  ? Center(
                      child: SingleChildScrollView(
                        child: Padding(
                          padding: const EdgeInsets.all(32.0),
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Container(
                                padding: const EdgeInsets.all(24),
                                decoration: const BoxDecoration(
                                  color: Color(0xFFEEF2FF),
                                  shape: BoxShape.circle,
                                ),
                                child: const Icon(
                                  Icons.post_add_outlined,
                                  size: 48,
                                  color: Color(0xFF4F46E5),
                                ),
                              ),
                              const SizedBox(height: 16),
                              const Text(
                                'No Claims Filed Yet',
                                style: TextStyle(
                                  fontSize: 18,
                                  fontWeight: FontWeight.bold,
                                  color: Color(0xFF0F172A),
                                ),
                              ),
                              const SizedBox(height: 8),
                              const Text(
                                'Tap "+ File New Claim" to upload your Policy PDF, capture geotagged photo proof, and submit claim details.',
                                textAlign: TextAlign.center,
                                style: TextStyle(color: Colors.grey, fontSize: 13),
                              ),
                              const SizedBox(height: 24),
                              ElevatedButton.icon(
                                style: ElevatedButton.styleFrom(
                                  backgroundColor: const Color(0xFF4F46E5),
                                  padding: const EdgeInsets.symmetric(
                                      horizontal: 24, vertical: 12),
                                  shape: RoundedRectangleBorder(
                                    borderRadius: BorderRadius.circular(12),
                                  ),
                                ),
                                onPressed: _startNewClaimFlow,
                                icon: const Icon(Icons.add, color: Colors.white),
                                label: const Text(
                                  'File First Claim',
                                  style: TextStyle(
                                      color: Colors.white,
                                      fontWeight: FontWeight.bold),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    )
                  : ListView(
                      padding: const EdgeInsets.fromLTRB(16, 8, 16, 90),
                      children: [
                        if (_drafts.isNotEmpty) ...[
                          const Padding(
                            padding: EdgeInsets.only(bottom: 8, top: 4),
                            child: Text(
                              'OFFLINE DRAFTS',
                              style: TextStyle(
                                fontSize: 11,
                                fontWeight: FontWeight.bold,
                                color: Color(0xFFD97706),
                                letterSpacing: 1.0,
                              ),
                            ),
                          ),
                          ..._drafts.map(_buildDraftCard),
                          const SizedBox(height: 12),
                        ],
                        ..._filteredClaims.map(_buildClaimCard),
                      ],
                    ),
            ),
          ],
        ),
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _startNewClaimFlow,
        backgroundColor: const Color(0xFF4F46E5),
        icon: const Icon(Icons.add_a_photo, color: Colors.white),
        label: const Text(
          'File New Claim',
          style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold),
        ),
      ),
    );
  }

  Widget _buildFilterChip(String label, int count) {
    final isSelected = _selectedFilter == label;
    return Padding(
      padding: const EdgeInsets.only(right: 8.0),
      child: FilterChip(
        selected: isSelected,
        label: Text('$label ($count)'),
        labelStyle: TextStyle(
          color: isSelected ? Colors.white : const Color(0xFF475569),
          fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
          fontSize: 12,
        ),
        backgroundColor: Colors.white,
        selectedColor: const Color(0xFF4F46E5),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
          side: BorderSide(
            color: isSelected ? const Color(0xFF4F46E5) : Colors.grey.shade300,
          ),
        ),
        onSelected: (bool selected) {
          setState(() => _selectedFilter = label);
        },
      ),
    );
  }

  Widget _buildDraftCard(DraftClaim d) {
    final hasPhoto = d.photoPath != null && d.photoPath!.isNotEmpty && File(d.photoPath!).existsSync();
    final title = d.itemName.isNotEmpty ? d.itemName : 'Untitled draft';
    return GestureDetector(
      onTap: () => _resumeDraft(d),
      child: Container(
        margin: const EdgeInsets.only(bottom: 12),
        decoration: BoxDecoration(
          color: const Color(0xFFFFFBEB),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: const Color(0xFFF59E0B)),
        ),
        child: Padding(
          padding: const EdgeInsets.all(14.0),
          child: Row(
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(10),
                child: hasPhoto
                    ? Image.file(File(d.photoPath!), width: 54, height: 54, fit: BoxFit.cover)
                    : Container(
                        width: 54,
                        height: 54,
                        color: const Color(0xFFFDE68A),
                        child: const Icon(Icons.photo_camera_back, color: Color(0xFFB45309)),
                      ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                          decoration: BoxDecoration(
                            color: const Color(0xFFF59E0B),
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: const Text('DRAFT • OFFLINE',
                              style: TextStyle(fontSize: 9, fontWeight: FontWeight.bold, color: Colors.white)),
                        ),
                        const Spacer(),
                        const Icon(Icons.cloud_off, size: 16, color: Color(0xFFB45309)),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Text(title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Color(0xFF0F172A))),
                    const SizedBox(height: 2),
                    Text(
                      '${d.userCategory ?? ''}${d.timestamp.isNotEmpty ? ' • ${d.timestamp}' : ''}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 11, color: Color(0xFF92400E)),
                    ),
                    const SizedBox(height: 2),
                    const Text('Tap to finish & submit when online',
                        style: TextStyle(fontSize: 10, color: Color(0xFFB45309), fontWeight: FontWeight.bold)),
                  ],
                ),
              ),
              IconButton(
                icon: const Icon(Icons.delete_outline, color: Color(0xFFB45309)),
                tooltip: 'Discard draft',
                onPressed: () => _deleteDraft(d),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildClaimCard(ClaimRecord claim) {
    Color statusBgColor;
    Color statusTextColor;
    String statusText;

    switch (claim.status) {
      case ClaimStatus.submitted:
        statusBgColor = const Color(0xFFECFDF5);
        statusTextColor = const Color(0xFF059669);
        statusText = 'Submitted';
        break;
      case ClaimStatus.pending:
        statusBgColor = const Color(0xFFFFFBEB);
        statusTextColor = const Color(0xFFD97706);
        statusText = 'Pending (AI Processing)';
        break;
      case ClaimStatus.review:
        statusBgColor = const Color(0xFFEFF6FF);
        statusTextColor = const Color(0xFF2563EB);
        statusText = 'Review (Draft Ready)';
        break;
      case ClaimStatus.awaitingDocuments:
        statusBgColor = const Color(0xFFFEF2F2);
        statusTextColor = const Color(0xFFDC2626);
        final n = claim.lor?.blockingMissing.length ?? 0;
        statusText = n > 0
            ? 'Documents needed ($n)'
            : 'Documents needed';
        break;
    }

    return GestureDetector(
      onTap: () => _onClaimTap(claim),
      child: Container(
        margin: const EdgeInsets.only(bottom: 12),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: Colors.grey.shade200),
        ),
        child: Padding(
          padding: const EdgeInsets.all(16.0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    claim.id,
                    style: const TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.bold,
                      color: Colors.grey,
                    ),
                  ),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                    decoration: BoxDecoration(
                      color: statusBgColor,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(
                      statusText,
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                        color: statusTextColor,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                claim.itemName,
                style: const TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.bold,
                  color: Color(0xFF0F172A),
                ),
              ),
              const SizedBox(height: 4),
              Text(
                'Category: ${claim.category} (${claim.itemType}) • Loss Date: ${DateFormat('yyyy-MM-dd').format(claim.lossDate)}',
                style: const TextStyle(fontSize: 12, color: Colors.grey),
              ),
              if (claim.category == 'Commercial' && claim.businessType != null) ...[
                const SizedBox(height: 2),
                Text(
                  'Business: ${claim.businessType} | GSTIN: ${claim.gstinNumber ?? "N/A"}',
                  style: const TextStyle(fontSize: 11, color: Color(0xFF4F46E5)),
                ),
              ],
              const SizedBox(height: 10),
              const Divider(height: 1),
              const SizedBox(height: 8),
              Row(
                children: [
                  const Icon(Icons.pin_drop, size: 14, color: Colors.redAccent),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(
                      claim.geotag,
                      style: const TextStyle(fontSize: 11, color: Colors.black87),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  if (claim.status == ClaimStatus.review)
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                      decoration: const BoxDecoration(
                        color: Color(0xFFEEF2FF),
                        borderRadius: BorderRadius.all(Radius.circular(6)),
                      ),
                      child: const Text('Tap to Review >', style: TextStyle(fontSize: 10, color: Color(0xFF4F46E5), fontWeight: FontWeight.bold)),
                    )
                  else
                    Text(
                      claim.timestamp,
                      style: const TextStyle(fontSize: 10, color: Colors.grey),
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

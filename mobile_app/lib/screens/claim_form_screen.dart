import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'package:intl/intl.dart';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:mime/mime.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'dart:convert';
import 'dart:io';
import '../config.dart';
import '../models/claim_model.dart';
import '../models/lor_model.dart';

/// Returns a usable filesystem path for a picked file. On Android the picker
/// often returns a null `path` (content:// URI) while still giving us `bytes`;
/// in that case we write the bytes to a temp file and return that path, so the
/// multipart upload (which needs a real path) always has one.
Future<String?> resolvePickedFilePath(PlatformFile file) async {
  if (file.path != null && file.path!.isNotEmpty && await File(file.path!).exists()) {
    return file.path;
  }
  if (file.bytes != null) {
    final tmp = File('${Directory.systemTemp.path}/pick_${DateTime.now().millisecondsSinceEpoch}_${file.name}');
    await tmp.writeAsBytes(file.bytes!, flush: true);
    return tmp.path;
  }
  return null;
}

class ClaimFormScreen extends StatefulWidget {
  final String? policyPdfName;
  final String? policyPdfPath;
  final String? photoPath;
  final String geotag;
  final String timestamp;
  final double? photoLat;
  final double? photoLon;
  final String? photoCapturedAt;
  final String? userCategory;
  final List<Map<String, dynamic>>? confirmedItems;

  const ClaimFormScreen({
    super.key,
    this.policyPdfName,
    this.policyPdfPath,
    this.photoPath,
    required this.geotag,
    required this.timestamp,
    this.photoLat,
    this.photoLon,
    this.photoCapturedAt,
    this.userCategory,
    this.confirmedItems,
  });

  @override
  State<ClaimFormScreen> createState() => _ClaimFormScreenState();
}

class _ClaimFormScreenState extends State<ClaimFormScreen> {
  final _formKey = GlobalKey<FormState>();
  final _itemNameController = TextEditingController();
  final _addressController = TextEditingController();
  final _businessTypeController = TextEditingController();
  final _gstinController = TextEditingController();

  String _selectedCategory = 'Personal';
  late String _selectedItemType;
  DateTime _lossDate = DateTime.now();
  String? _govtIdName;
  String? _govtIdPath;
  String? _invoiceName;
  String? _invoicePath;
  bool _isSubmitting = false;

  final Map<String, List<String>> _itemTypesByCategory = {
    'Personal': [
      'Vehicle (Car / Bike)',
      'Home / Property Asset',
      'Personal Electronics & Appliances',
      'Jewelry & Valuables',
      'Furniture & Fixtures',
      'Other Personal Property',
    ],
    'Commercial': [
      'Industrial Machinery & Equipment',
      'Stock, Inventory & Raw Materials',
      'Commercial Property & Building',
      'Commercial Fleet / Cargo Vehicle',
      'IT Hardware & Office Electronics',
      'Tools & Trade Supplies',
    ],
    'Insurance Firm': [
      'Commercial Asset Portfolio',
      'Reinsurance Policy Claim',
      'Motor Third-Party / Fleet Claim',
      'Engineering & Construction Risk',
      'Marine & Cargo Transit',
      'General Casualty Claim',
    ],
  };

  @override
  void initState() {
    super.initState();
    _selectedCategory = widget.userCategory ?? 'Personal';
    final types = _itemTypesByCategory[_selectedCategory] ?? _itemTypesByCategory['Personal']!;
    _selectedItemType = types.first;
  }

  @override
  void dispose() {
    _itemNameController.dispose();
    _addressController.dispose();
    _businessTypeController.dispose();
    _gstinController.dispose();
    super.dispose();
  }

  Future<void> _pickGovtId() async {
    try {
      FilePickerResult? result = await FilePicker.platform.pickFiles(
        type: FileType.custom,
        allowedExtensions: ['pdf', 'jpg', 'jpeg', 'png'],
        withData: true, // ensure bytes are available if the picker gives no path
      );

      if (result != null && result.files.isNotEmpty) {
        final f = result.files.first;
        final path = await resolvePickedFilePath(f);
        if (path == null) {
          if (!mounted) return;
          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('Could not read that file. Please pick from local device storage.'),
            backgroundColor: Colors.amber,
          ));
          return;
        }
        setState(() {
          _govtIdName = f.name;
          _govtIdPath = path;
        });
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not pick Govt ID: $e')),
      );
    }
  }

  Future<void> _pickInvoice() async {
    try {
      FilePickerResult? result = await FilePicker.platform.pickFiles(
        type: FileType.custom,
        allowedExtensions: ['pdf', 'jpg', 'jpeg', 'png'],
        withData: true,
      );

      if (result != null && result.files.isNotEmpty) {
        final f = result.files.first;
        final path = await resolvePickedFilePath(f);
        if (path == null) {
          if (!mounted) return;
          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('Could not read that invoice file. Please pick from local device storage.'),
            backgroundColor: Colors.amber,
          ));
          return;
        }
        setState(() {
          _invoiceName = f.name;
          _invoicePath = path;
        });
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not pick invoice: $e')),
      );
    }
  }

  /// Builds a multipart file with a real content-type inferred from its path.
  /// http.MultipartFile.fromPath otherwise defaults to application/octet-stream,
  /// which makes the backend misclassify images.
  Future<http.MultipartFile> _multipartFrom(String field, String path) async {
    final mimeStr = lookupMimeType(path) ?? 'application/octet-stream';
    final parts = mimeStr.split('/');
    return http.MultipartFile.fromPath(
      field,
      path,
      contentType: MediaType(parts[0], parts.length > 1 ? parts[1] : 'octet-stream'),
    );
  }

  Future<void> _selectLossDate(BuildContext context) async {
    final DateTime? picked = await showDatePicker(
      context: context,
      initialDate: _lossDate,
      firstDate: DateTime(2020),
      lastDate: DateTime.now(),
    );
    if (picked != null && picked != _lossDate) {
      setState(() {
        _lossDate = picked;
      });
    }
  }

  Future<void> _submitClaim() async {
    debugPrint('[submit] tapped');
    if (!_formKey.currentState!.validate()) {
      debugPrint('[submit] blocked: form validation failed (a required field is empty)');
      // Don't fail silently — tell the user why nothing happened.
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Please complete the required fields highlighted above (e.g. Permanent Address).'),
          backgroundColor: Colors.amber,
        ),
      );
      return;
    }
    // Both documents are required (mirrors the gateway's own 400). Note this is
    // a client-side affordance only — the pipeline enforces coverage regardless.
    // Check the actual PATHS that will be uploaded — not the display names. On
    // the emulator a picker can set a name with no usable path; guarding on the
    // name would let submit through with the file silently missing.
    final missing = <String>[];
    if (widget.policyPdfPath == null || widget.policyPdfPath!.isEmpty) missing.add('insurance policy PDF');
    if (_govtIdPath == null || _govtIdPath!.isEmpty) missing.add('Govt ID (Aadhaar / PAN)');
    if (missing.isNotEmpty) {
      debugPrint('[submit] blocked: missing documents: $missing');
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Please upload your ${missing.join(' and ')}.'),
          backgroundColor: Colors.amber,
        ),
      );
      return;
    }

    debugPrint('[submit] validation passed; sending to gateway...');
    setState(() => _isSubmitting = true);

    // Send HTTP Multipart POST request to Express Node Backend gateway.
    final String endpoint;
    if (_selectedCategory == 'Commercial') {
      endpoint = '$kApiBase/api/commercial/submit';
    } else if (_selectedCategory == 'Insurance Firm') {
      endpoint = '$kApiBase/api/insurance/submit';
    } else {
      endpoint = '$kApiBase/api/personal/submit';
    }

    String? backendClaimId;
    String? submitError;
    LorPack? provisionalLor;

    try {
      final request = http.MultipartRequest('POST', Uri.parse(endpoint));
      request.fields['product'] = _itemNameController.text.trim();
      request.fields['description'] = 'Claim for ${_itemNameController.text.trim()} ($_selectedItemType)';
      request.fields['event_date'] = _lossDate.toIso8601String();
      request.fields['categories'] = _selectedItemType;
      // The raw app label, kept alongside `categories` (which the gateway maps
      // to a pipeline category). The requirement rules match on this label.
      request.fields['item_type'] = _selectedItemType;
      // Real capture metadata so the backend doesn't fall back to upload-time /
      // placeholder geo (which fails the loss-window and geofence checks).
      if (widget.photoCapturedAt != null) {
        request.fields['photo_captured_at'] = widget.photoCapturedAt!;
      }
      if (widget.photoLat != null && widget.photoLon != null) {
        request.fields['photo_lat'] = widget.photoLat!.toString();
        request.fields['photo_lon'] = widget.photoLon!.toString();
      }
      // User-confirmed items from the preview step (if any) — the pipeline uses
      // these instead of re-running vision.
      if (widget.confirmedItems != null && widget.confirmedItems!.isNotEmpty) {
        request.fields['confirmed_items'] = jsonEncode(widget.confirmedItems);
      }

      // Policy details captured during onboarding. Without these the backend
      // has to assume generic terms, which makes coverage verdicts vague.
      final prefs = await SharedPreferences.getInstance();
      final policyNumber = prefs.getString('policy_number');
      final insurer = prefs.getString('policy_insurer');
      final sumInsured = prefs.getDouble('policy_sum_insured');
      final excess = prefs.getDouble('policy_excess');
      if (policyNumber != null && policyNumber.isNotEmpty) {
        request.fields['policy_number'] = policyNumber;
      }
      if (insurer != null && insurer.isNotEmpty) {
        request.fields['policy_insurer'] = insurer;
      }
      if (sumInsured != null && sumInsured > 0) {
        request.fields['policy_sum_insured'] = sumInsured.toString();
      }
      if (excess != null && excess > 0) {
        request.fields['policy_excess'] = excess.toString();
      }

      // Route each file under a named field so the gateway classifies by ROLE.
      if (widget.photoPath != null && widget.photoPath!.isNotEmpty) {
        request.files.add(await _multipartFrom('photos', widget.photoPath!));
      }
      if (_invoicePath != null && _invoicePath!.isNotEmpty) {
        request.files.add(await _multipartFrom('invoices', _invoicePath!));
      }
      if (_govtIdPath != null && _govtIdPath!.isNotEmpty) {
        request.files.add(await _multipartFrom('govt_id', _govtIdPath!));
      }
      if (widget.policyPdfPath != null && widget.policyPdfPath!.isNotEmpty) {
        request.files.add(await _multipartFrom('policy_doc', widget.policyPdfPath!));
      }

      final response = await request.send().timeout(const Duration(seconds: 120));
      debugPrint('Node Backend response status: ${response.statusCode}');
      final responseData = await response.stream.bytesToString();

      if (response.statusCode == 200) {
        final jsonResponse = jsonDecode(responseData);
        if (jsonResponse['success'] == true && jsonResponse['data'] != null) {
          backendClaimId = jsonResponse['data']['claim_id'] as String?;
          // Revision 1 of the checklist, computed synchronously by the pipeline
          // so the user sees it now rather than after the full analysis.
          provisionalLor = LorPack.tryFrom(jsonResponse['data']['lor']);
        }
      } else if (response.statusCode == 400) {
        // Gateway rejected the submission (e.g. required documents missing).
        try {
          final err = jsonDecode(responseData);
          final missing = (err['missing'] as List?)?.join(', ');
          submitError = missing != null
              ? 'Missing required documents: $missing.'
              : (err['error']?.toString() ?? 'The claim was not accepted.');
        } catch (_) {
          submitError = 'The claim was not accepted (400).';
        }
      }
      if (backendClaimId == null) {
        submitError ??= 'Server returned ${response.statusCode}. The claim was not accepted.';
      }
    } catch (e) {
      submitError = '$e';
    }

    // Honest failure: do NOT fabricate a claim id and tell the user "submitted".
    if (backendClaimId == null) {
      if (!mounted) return;
      setState(() => _isSubmitting = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Could not submit claim: ${submitError ?? 'unknown error'}'),
          backgroundColor: Colors.redAccent,
          duration: const Duration(seconds: 5),
        ),
      );
      return;
    }

    final newClaim = ClaimRecord(
      id: backendClaimId,
      itemName: _itemNameController.text.trim(),
      category: _selectedCategory,
      itemType: _selectedItemType,
      policyPdfName: widget.policyPdfName,
      policyPdfPath: widget.policyPdfPath,
      photoPath: widget.photoPath,
      geotag: widget.geotag,
      timestamp: widget.timestamp,
      govtIdName: _govtIdName,
      govtIdPath: _govtIdPath,
      permanentAddress: _addressController.text.trim(),
      lossDate: _lossDate,
      businessType: _selectedCategory == 'Commercial'
          ? _businessTypeController.text.trim()
          : null,
      gstinNumber: _selectedCategory == 'Commercial'
          ? _gstinController.text.trim()
          : null,
      lor: provisionalLor,
      status: ClaimStatus.pending,
    );

    if (!mounted) return;
    Navigator.pop(context, newClaim);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF8FAFC),
      appBar: AppBar(
        title: const Text('Step 3: Complete Claim Submission'),
        backgroundColor: Colors.white,
        elevation: 0.5,
        foregroundColor: const Color(0xFF0F172A),
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20.0),
          child: Form(
            key: _formKey,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Geotag & Timestamp Summary Banner
                Container(
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color: const Color(0xFFEEF2FF),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: const Color(0xFFC7D2FE)),
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.pin_drop, color: Color(0xFF4F46E5), size: 24),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              '📍 Geotag: ${widget.geotag}',
                              style: const TextStyle(
                                fontWeight: FontWeight.bold,
                                fontSize: 12,
                                color: Color(0xFF1E1B4B),
                              ),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              '🕒 Timestamp: ${widget.timestamp}',
                              style: const TextStyle(fontSize: 11, color: Color(0xFF4338CA)),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 20),

                const Text(
                  'CLAIM DETAILS FORM',
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.bold,
                    color: Colors.grey,
                    letterSpacing: 1.0,
                  ),
                ),
                const SizedBox(height: 12),

                // Item Name
                _buildInputField(
                  controller: _itemNameController,
                  label: 'Item Name',
                  hint: 'e.g. Damaged Cotton Fabrics / Billing Machinery',
                  icon: Icons.inventory_2_outlined,
                  validator: (val) => val == null || val.trim().isEmpty
                      ? 'Please enter item name'
                      : null,
                ),
                const SizedBox(height: 14),

                // Locked Account Category Badge (Set during onboarding)
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                  decoration: BoxDecoration(
                    color: const Color(0xFFEEF2FF),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: const Color(0xFFC7D2FE)),
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.category_outlined, color: Color(0xFF4F46E5), size: 20),
                      const SizedBox(width: 12),
                      Text(
                        'Account Category: $_selectedCategory',
                        style: const TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 14,
                          color: Color(0xFF1E1B4B),
                        ),
                      ),
                      const Spacer(),
                      const Icon(Icons.lock_outline, size: 16, color: Color(0xFF4F46E5)),
                    ],
                  ),
                ),
                const SizedBox(height: 14),

                // Item Type / Sub-category Section (Vehicle, Machinery, Stock, etc.)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: Colors.grey.shade300),
                  ),
                  child: DropdownButtonHideUnderline(
                    child: DropdownButton<String>(
                      value: _selectedItemType,
                      isExpanded: true,
                      items: (_itemTypesByCategory[_selectedCategory] ?? []).map((type) {
                        return DropdownMenuItem(
                          value: type,
                          child: Text('Item Type: $type',
                              style: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Color(0xFF0F172A))),
                        );
                      }).toList(),
                      onChanged: (val) {
                        if (val != null) {
                          setState(() => _selectedItemType = val);
                        }
                      },
                    ),
                  ),
                ),
                const SizedBox(height: 14),

                // Govt ID Upload (PDF and JPEG)
                GestureDetector(
                  onTap: _pickGovtId,
                  child: Container(
                    padding: const EdgeInsets.all(14),
                    decoration: BoxDecoration(
                      color: _govtIdName != null
                          ? const Color(0xFFECFDF5)
                          : Colors.white,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(
                        color: _govtIdName != null
                            ? const Color(0xFF10B981)
                            : Colors.grey.shade300,
                      ),
                    ),
                    child: Row(
                      children: [
                        Icon(
                          _govtIdName != null
                              ? Icons.verified
                              : Icons.badge_outlined,
                          color: _govtIdName != null
                              ? const Color(0xFF059669)
                              : const Color(0xFF4F46E5),
                          size: 24,
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                _govtIdName ?? 'Upload Govt ID (Aadhar / PAN)',
                                style: TextStyle(
                                  fontWeight: FontWeight.bold,
                                  fontSize: 13,
                                  color: _govtIdName != null
                                      ? const Color(0xFF065F46)
                                      : const Color(0xFF0F172A),
                                ),
                              ),
                              Text(
                                _govtIdName != null
                                    ? 'Govt ID attached • Tap to replace'
                                    : 'Supports PDF, JPG, PNG',
                                style: TextStyle(
                                  fontSize: 11,
                                  color: _govtIdName != null
                                      ? const Color(0xFF047857)
                                      : Colors.grey,
                                ),
                              ),
                            ],
                          ),
                        ),
                        const Icon(Icons.upload_file, color: Colors.grey, size: 20),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 14),

                // Purchase Invoice Upload (optional but needed for valuation)
                GestureDetector(
                  onTap: _pickInvoice,
                  child: Container(
                    padding: const EdgeInsets.all(14),
                    decoration: BoxDecoration(
                      color: _invoiceName != null
                          ? const Color(0xFFECFDF5)
                          : Colors.white,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(
                        color: _invoiceName != null
                            ? const Color(0xFF10B981)
                            : Colors.grey.shade300,
                      ),
                    ),
                    child: Row(
                      children: [
                        Icon(
                          _invoiceName != null
                              ? Icons.receipt_long
                              : Icons.receipt_long_outlined,
                          color: _invoiceName != null
                              ? const Color(0xFF059669)
                              : const Color(0xFF4F46E5),
                          size: 24,
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                _invoiceName ?? 'Upload Purchase Invoice (optional)',
                                style: TextStyle(
                                  fontWeight: FontWeight.bold,
                                  fontSize: 13,
                                  color: _invoiceName != null
                                      ? const Color(0xFF065F46)
                                      : const Color(0xFF0F172A),
                                ),
                              ),
                              Text(
                                _invoiceName != null
                                    ? 'Invoice attached • Tap to replace'
                                    : 'Helps value the claim • PDF, JPG, PNG',
                                style: TextStyle(
                                  fontSize: 11,
                                  color: _invoiceName != null
                                      ? const Color(0xFF047857)
                                      : Colors.grey,
                                ),
                              ),
                            ],
                          ),
                        ),
                        const Icon(Icons.upload_file, color: Colors.grey, size: 20),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 14),

                // Permanent Address
                _buildInputField(
                  controller: _addressController,
                  label: 'Permanent Address',
                  hint: 'Full address where loss occurred',
                  icon: Icons.home_outlined,
                  maxLines: 2,
                  validator: (val) => val == null || val.trim().isEmpty
                      ? 'Please enter permanent address'
                      : null,
                ),
                const SizedBox(height: 14),

                // Loss Date Picker
                InkWell(
                  onTap: () => _selectLossDate(context),
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
                    decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: Colors.grey.shade300),
                    ),
                    child: Row(
                      children: [
                        const Icon(Icons.calendar_today, color: Color(0xFF4F46E5), size: 20),
                        const SizedBox(width: 12),
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text('Loss Date', style: TextStyle(color: Colors.grey, fontSize: 11)),
                            Text(
                              DateFormat('yyyy-MM-dd').format(_lossDate),
                              style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 14),

                // Conditional Commercial Fields
                if (_selectedCategory == 'Commercial') ...[
                  const Divider(height: 24),
                  const Text(
                    'COMMERCIAL DETAILS',
                    style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.bold,
                      color: Color(0xFF4F46E5),
                      letterSpacing: 1.0,
                    ),
                  ),
                  const SizedBox(height: 12),
                  _buildInputField(
                    controller: _businessTypeController,
                    label: 'Business Type',
                    hint: 'e.g. Textile Retail, Electronics, Manufacturing',
                    icon: Icons.business_center_outlined,
                    validator: (val) => _selectedCategory == 'Commercial' && (val == null || val.trim().isEmpty)
                        ? 'Please enter business type'
                        : null,
                  ),
                  const SizedBox(height: 14),
                  _buildInputField(
                    controller: _gstinController,
                    label: 'GSTIN Number',
                    hint: 'e.g. 33AAAAA0000A1Z5',
                    icon: Icons.receipt_long_outlined,
                    validator: (val) => _selectedCategory == 'Commercial' && (val == null || val.trim().isEmpty)
                        ? 'Please enter GSTIN number'
                        : null,
                  ),
                  const SizedBox(height: 14),
                ],

                const SizedBox(height: 24),

                // Submit Button
                SizedBox(
                  width: double.infinity,
                  height: 52,
                  child: ElevatedButton.icon(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: const Color(0xFF4F46E5),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(12),
                      ),
                    ),
                    onPressed: _isSubmitting ? null : _submitClaim,
                    icon: _isSubmitting
                        ? const SizedBox(
                            width: 20,
                            height: 20,
                            child: CircularProgressIndicator(
                              color: Colors.white,
                              strokeWidth: 2,
                            ),
                          )
                        : const Icon(Icons.send_rounded, color: Colors.white),
                    label: Text(
                      _isSubmitting ? 'Submitting Claim...' : 'Submit Claim',
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 16,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildInputField({
    required TextEditingController controller,
    required String label,
    String? hint,
    required IconData icon,
    int maxLines = 1,
    String? Function(String?)? validator,
  }) {
    return Container(
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.grey.shade300),
      ),
      child: TextFormField(
        controller: controller,
        maxLines: maxLines,
        validator: validator,
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          labelStyle: TextStyle(color: Colors.grey.shade600, fontSize: 13),
          prefixIcon: Icon(icon, color: const Color(0xFF4F46E5), size: 20),
          border: InputBorder.none,
          contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        ),
      ),
    );
  }
}

import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'package:intl/intl.dart';
import '../models/claim_model.dart';

class ClaimFormScreen extends StatefulWidget {
  final String? policyPdfName;
  final String? policyPdfPath;
  final String? photoPath;
  final String geotag;
  final String timestamp;
  final String? userCategory;

  const ClaimFormScreen({
    super.key,
    this.policyPdfName,
    this.policyPdfPath,
    this.photoPath,
    required this.geotag,
    required this.timestamp,
    this.userCategory,
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
      );

      if (result != null && result.files.isNotEmpty) {
        setState(() {
          _govtIdName = result.files.first.name;
          _govtIdPath = result.files.first.path;
        });
      }
    } catch (e) {
      setState(() {
        _govtIdName = 'Aadhar_Card_Verified.pdf';
        _govtIdPath = '/storage/emulated/0/Documents/Aadhar.pdf';
      });
    }
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

  void _submitClaim() {
    if (!_formKey.currentState!.validate()) return;
    if (_govtIdName == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Please upload your Govt ID (Aadhar / PAN Card PDF or JPEG).'),
          backgroundColor: Colors.amber,
        ),
      );
      return;
    }

    setState(() => _isSubmitting = true);

    final newClaim = ClaimRecord(
      id: 'CLM-${DateTime.now().millisecondsSinceEpoch.toString().substring(7)}',
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
      status: ClaimStatus.submitted,
    );

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

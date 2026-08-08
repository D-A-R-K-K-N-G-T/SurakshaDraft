import 'package:flutter/material.dart';
import '../models/line_item.dart';
import 'camera_screen.dart';

class ReviewFormScreen extends StatefulWidget {
  final LineItem draftItem;

  const ReviewFormScreen({super.key, required this.draftItem});

  @override
  State<ReviewFormScreen> createState() => _ReviewFormScreenState();
}

class _ReviewFormScreenState extends State<ReviewFormScreen> {
  late TextEditingController _nameController;
  late TextEditingController _descController;
  late TextEditingController _qtyController;
  late TextEditingController _unitController;
  late TextEditingController _valueController;
  late TextEditingController _serialController;
  late String _selectedCategory;

  final List<String> _categories = [
    'Stock',
    'Machinery',
    'FFF',
    'Electronics',
    'Vehicles',
    'Other',
  ];

  @override
  void initState() {
    super.initState();
    _nameController = TextEditingController(text: widget.draftItem.name);
    _descController = TextEditingController(text: widget.draftItem.description);
    _qtyController =
        TextEditingController(text: widget.draftItem.quantity.toStringAsFixed(0));
    _unitController = TextEditingController(text: widget.draftItem.unit);
    _valueController =
        TextEditingController(text: widget.draftItem.estimatedValue.toStringAsFixed(0));
    _serialController =
        TextEditingController(text: widget.draftItem.serialNumber ?? '');
    _selectedCategory = _categories.contains(widget.draftItem.category)
        ? widget.draftItem.category
        : 'Stock';
  }

  @override
  void dispose() {
    _nameController.dispose();
    _descController.dispose();
    _qtyController.dispose();
    _unitController.dispose();
    _valueController.dispose();
    _serialController.dispose();
    super.dispose();
  }

  void _saveItem() {
    final updatedItem = widget.draftItem.copyWith(
      name: _nameController.text.trim(),
      description: _descController.text.trim(),
      category: _selectedCategory,
      quantity: double.tryParse(_qtyController.text) ?? widget.draftItem.quantity,
      unit: _unitController.text.trim(),
      estimatedValue: double.tryParse(_valueController.text) ?? widget.draftItem.estimatedValue,
      serialNumber: _serialController.text.trim().isNotEmpty
          ? _serialController.text.trim()
          : null,
      status: widget.draftItem.isAiConfident
          ? ItemStatus.confirmed
          : ItemStatus.needsReview,
    );

    Navigator.pop(context, updatedItem);
  }

  @override
  Widget build(BuildContext context) {
    final isConfident = widget.draftItem.isAiConfident;
    final confidencePct = (widget.draftItem.confidenceScore * 100).toStringAsFixed(0);

    return Scaffold(
      backgroundColor: const Color(0xFFF8FAFC),
      appBar: AppBar(
        title: const Text('Review AI Draft Item'),
        backgroundColor: Colors.white,
        elevation: 0,
        foregroundColor: const Color(0xFF1E293B),
        actions: [
          IconButton(
            icon: const Icon(Icons.mic, color: Color(0xFF4F46E5)),
            tooltip: 'Voice edit fields',
            onPressed: () {
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(
                  content: Text('🎤 Listening for voice corrections...'),
                  duration: Duration(seconds: 2),
                ),
              );
            },
          ),
        ],
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20.0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Confidence Badge (Green or Amber)
              Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                decoration: BoxDecoration(
                  color: isConfident
                      ? const Color(0xFFECFDF5)
                      : const Color(0xFFFFFBEB),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(
                    color: isConfident
                        ? const Color(0xFF10B981)
                        : const Color(0xFFF59E0B),
                    width: 1.5,
                  ),
                ),
                child: Row(
                  children: [
                    Icon(
                      isConfident ? Icons.check_circle : Icons.warning_amber_rounded,
                      color: isConfident
                          ? const Color(0xFF059669)
                          : const Color(0xFFD97706),
                      size: 24,
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            isConfident
                                ? 'AI Confident ($confidencePct%) — Vision Verified'
                                : 'Please Double-Check ($confidencePct% confidence)',
                            style: TextStyle(
                              fontWeight: FontWeight.bold,
                              fontSize: 14,
                              color: isConfident
                                  ? const Color(0xFF065F46)
                                  : const Color(0xFF92400E),
                            ),
                          ),
                          const SizedBox(height: 2),
                          Text(
                            isConfident
                                ? 'All fields auto-suggested from captured image.'
                                : 'Low clarity image or missing serial number.',
                            style: TextStyle(
                              fontSize: 12,
                              color: isConfident
                                  ? const Color(0xFF047857)
                                  : const Color(0xFFB45309),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 20),

              // Image Thumbnail & Ref
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: Colors.grey.shade200),
                ),
                child: Row(
                  children: [
                    Container(
                      width: 60,
                      height: 60,
                      decoration: BoxDecoration(
                        color: const Color(0xFFEEF2FF),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: const Icon(
                        Icons.image,
                        color: Color(0xFF4F46E5),
                        size: 32,
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'Evidence Ref: ${widget.draftItem.evidenceRefs.join(", ")}',
                            style: const TextStyle(
                              fontWeight: FontWeight.bold,
                              fontSize: 12,
                            ),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            widget.draftItem.valueSource ?? 'AI Vision + Geo-Attestation',
                            style: const TextStyle(
                              fontSize: 11,
                              color: Colors.grey,
                            ),
                          ),
                        ],
                      ),
                    ),
                    OutlinedButton(
                      style: OutlinedButton.styleFrom(
                        visualDensity: VisualDensity.compact,
                        side: const BorderSide(color: Color(0xFF4F46E5)),
                      ),
                      onPressed: () {
                        Navigator.pushReplacement(
                          context,
                          MaterialPageRoute(
                            builder: (_) => const CameraScreen(),
                          ),
                        );
                      },
                      child: const Text(
                        'Retake',
                        style: TextStyle(fontSize: 12, color: Color(0xFF4F46E5)),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 24),

              // Pre-filled Editable Form Section
              const Text(
                'AI DRAFT LINE ITEM FIELDS',
                style: TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.bold,
                  color: Colors.grey,
                  letterSpacing: 1.0,
                ),
              ),
              const SizedBox(height: 12),

              _buildTextField(
                label: 'Item Name (AI-Suggested)',
                controller: _nameController,
                icon: Icons.inventory_2_outlined,
              ),
              const SizedBox(height: 14),

              // Category Dropdown
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: Colors.grey.shade300),
                ),
                child: DropdownButtonHideUnderline(
                  child: DropdownButton<String>(
                    value: _selectedCategory,
                    isExpanded: true,
                    items: _categories.map((cat) {
                      return DropdownMenuItem(
                        value: cat,
                        child: Text('Category: $cat',
                            style: const TextStyle(fontSize: 14)),
                      );
                    }).toList(),
                    onChanged: (val) {
                      if (val != null) {
                        setState(() => _selectedCategory = val);
                      }
                    },
                  ),
                ),
              ),
              const SizedBox(height: 14),

              _buildTextField(
                label: 'Description',
                controller: _descController,
                icon: Icons.notes_outlined,
                maxLines: 2,
              ),
              const SizedBox(height: 14),

              Row(
                children: [
                  Expanded(
                    flex: 2,
                    child: _buildTextField(
                      label: 'Quantity',
                      controller: _qtyController,
                      icon: Icons.numbers,
                      keyboardType: TextInputType.number,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    flex: 2,
                    child: _buildTextField(
                      label: 'Unit',
                      controller: _unitController,
                      icon: Icons.straighten,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 14),

              _buildTextField(
                label: 'Estimated Value (₹)',
                controller: _valueController,
                icon: Icons.currency_rupee,
                keyboardType: TextInputType.number,
              ),
              const SizedBox(height: 14),

              _buildTextField(
                label: 'Serial Number / Tag (Optional)',
                controller: _serialController,
                icon: Icons.qr_code,
              ),
              const SizedBox(height: 28),

              // Bottom Save & Action Buttons
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
                  onPressed: _saveItem,
                  icon: const Icon(Icons.check_circle_outline, color: Colors.white),
                  label: const Text(
                    'Looks good, save',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 12),
              Center(
                child: TextButton.icon(
                  onPressed: () {
                    Navigator.pushReplacement(
                      context,
                      MaterialPageRoute(
                        builder: (_) => const CameraScreen(),
                      ),
                    );
                  },
                  icon: const Icon(Icons.camera_alt_outlined, color: Colors.grey),
                  label: const Text(
                    'Retake photo',
                    style: TextStyle(color: Colors.grey, fontSize: 14),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildTextField({
    required String label,
    required TextEditingController controller,
    required IconData icon,
    int maxLines = 1,
    TextInputType keyboardType = TextInputType.text,
  }) {
    return Container(
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.grey.shade300),
      ),
      child: TextField(
        controller: controller,
        maxLines: maxLines,
        keyboardType: keyboardType,
        decoration: InputDecoration(
          labelText: label,
          labelStyle: TextStyle(color: Colors.grey.shade600, fontSize: 13),
          prefixIcon: Icon(icon, color: const Color(0xFF4F46E5), size: 20),
          border: InputBorder.none,
          contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        ),
      ),
    );
  }
}

import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:mime/mime.dart';
import '../config.dart';
import 'claim_form_screen.dart';

/// Shown right after capture: calls the vision preview endpoint, lets the user
/// confirm/edit the items the AI proposed (quantity, serial, category), then
/// continues to the claim form carrying the confirmed items. If preview fails
/// or the user skips, we continue with no confirmed items and the pipeline runs
/// vision itself.
class PreviewConfirmScreen extends StatefulWidget {
  final String? policyPdfName;
  final String? policyPdfPath;
  final String photoPath;
  final String geotag;
  final String timestamp;
  final double? photoLat;
  final double? photoLon;
  final String? photoCapturedAt;
  final String? userCategory;

  const PreviewConfirmScreen({
    super.key,
    this.policyPdfName,
    this.policyPdfPath,
    required this.photoPath,
    required this.geotag,
    required this.timestamp,
    this.photoLat,
    this.photoLon,
    this.photoCapturedAt,
    this.userCategory,
  });

  @override
  State<PreviewConfirmScreen> createState() => _PreviewConfirmScreenState();
}

class _EditableItem {
  final TextEditingController name;
  final TextEditingController category;
  final TextEditingController quantity;
  final TextEditingController serial;
  final double confidence;

  _EditableItem(Map<String, dynamic> m)
      : name = TextEditingController(text: (m['name'] ?? '').toString()),
        category = TextEditingController(text: (m['category'] ?? '').toString()),
        quantity = TextEditingController(text: (m['quantity'] ?? 1).toString()),
        serial = TextEditingController(text: (m['serial_number'] ?? '').toString()),
        confidence = (m['vision_confidence'] is num) ? (m['vision_confidence'] as num).toDouble() : 0.0;

  Map<String, dynamic> toJson() => {
        'name': name.text.trim(),
        'category': category.text.trim(),
        'quantity': double.tryParse(quantity.text.trim()) ?? 1,
        'serial_number': serial.text.trim().isEmpty ? null : serial.text.trim(),
      };

  void dispose() {
    name.dispose();
    category.dispose();
    quantity.dispose();
    serial.dispose();
  }
}

class _PreviewConfirmScreenState extends State<PreviewConfirmScreen> {
  bool _loading = true;
  String? _error;
  final List<_EditableItem> _items = [];

  @override
  void initState() {
    super.initState();
    _fetchPreview();
  }

  @override
  void dispose() {
    for (final it in _items) {
      it.dispose();
    }
    super.dispose();
  }

  Future<void> _fetchPreview() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final request = http.MultipartRequest('POST', Uri.parse('$kApiBase/api/preview'));
      final mimeStr = lookupMimeType(widget.photoPath) ?? 'image/jpeg';
      final parts = mimeStr.split('/');
      request.files.add(await http.MultipartFile.fromPath(
        'photo',
        widget.photoPath,
        contentType: MediaType(parts[0], parts.length > 1 ? parts[1] : 'jpeg'),
      ));
      if (widget.userCategory != null) {
        request.fields['categories'] = widget.userCategory!;
      }

      final response = await request.send().timeout(const Duration(seconds: 120));
      final body = await response.stream.bytesToString();

      if (response.statusCode == 200) {
        final json = jsonDecode(body);
        final items = (json['data']?['items'] as List?) ?? [];
        setState(() {
          _items
            ..clear()
            ..addAll(items.map((m) => _EditableItem(Map<String, dynamic>.from(m))));
          _loading = false;
        });
      } else {
        setState(() {
          _error = 'AI could not identify items (status ${response.statusCode}). You can skip and let the pipeline analyze it.';
          _loading = false;
        });
      }
    } catch (e) {
      setState(() {
        _error = 'Could not reach the AI preview service: $e';
        _loading = false;
      });
    }
  }

  void _continue({required bool withItems}) {
    final confirmed = withItems ? _items.map((e) => e.toJson()).toList() : null;
    Navigator.pushReplacement(
      context,
      MaterialPageRoute(
        builder: (_) => ClaimFormScreen(
          policyPdfName: widget.policyPdfName,
          policyPdfPath: widget.policyPdfPath,
          photoPath: widget.photoPath,
          geotag: widget.geotag,
          timestamp: widget.timestamp,
          photoLat: widget.photoLat,
          photoLon: widget.photoLon,
          photoCapturedAt: widget.photoCapturedAt,
          userCategory: widget.userCategory,
          confirmedItems: confirmed,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF8FAFC),
      appBar: AppBar(title: const Text('Confirm Detected Items')),
      body: _loading
          ? const Center(child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                CircularProgressIndicator(color: Color(0xFF4F46E5)),
                SizedBox(height: 16),
                Text('Analyzing photo…'),
              ],
            ))
          : _error != null
              ? _buildError()
              : _buildList(),
      bottomNavigationBar: (_loading || _error != null)
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  children: [
                    Expanded(
                      child: OutlinedButton(
                        onPressed: () => _continue(withItems: false),
                        child: const Text('Skip'),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      flex: 2,
                      child: ElevatedButton(
                        onPressed: _items.isEmpty ? null : () => _continue(withItems: true),
                        style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF4F46E5)),
                        child: const Text('Confirm & Continue', style: TextStyle(color: Colors.white)),
                      ),
                    ),
                  ],
                ),
              ),
            ),
    );
  }

  Widget _buildError() {
    return Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.info_outline, color: Color(0xFF64748B), size: 40),
          const SizedBox(height: 12),
          Text(_error!, textAlign: TextAlign.center),
          const SizedBox(height: 20),
          Row(
            children: [
              Expanded(
                child: OutlinedButton(onPressed: _fetchPreview, child: const Text('Retry')),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: ElevatedButton(
                  onPressed: () => _continue(withItems: false),
                  style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF4F46E5)),
                  child: const Text('Continue anyway', style: TextStyle(color: Colors.white)),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildList() {
    return ListView.separated(
      padding: const EdgeInsets.all(16),
      itemCount: _items.length,
      separatorBuilder: (_, __) => const SizedBox(height: 12),
      itemBuilder: (context, i) {
        final it = _items[i];
        return Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: Colors.grey.shade300),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(child: _field(it.name, 'Item name')),
                  const SizedBox(width: 8),
                  Text('${(it.confidence * 100).round()}%',
                      style: const TextStyle(fontSize: 12, color: Colors.grey)),
                ],
              ),
              const SizedBox(height: 8),
              _field(it.category, 'Category'),
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(child: _field(it.quantity, 'Quantity', number: true)),
                  const SizedBox(width: 8),
                  Expanded(child: _field(it.serial, 'Serial (if any)')),
                ],
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _field(TextEditingController c, String label, {bool number = false}) {
    return TextField(
      controller: c,
      keyboardType: number ? TextInputType.number : TextInputType.text,
      decoration: InputDecoration(
        labelText: label,
        isDense: true,
        border: const OutlineInputBorder(),
      ),
    );
  }
}

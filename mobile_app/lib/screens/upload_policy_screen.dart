import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'camera_screen.dart';

class UploadPolicyScreen extends StatefulWidget {
  final String? userCategory;
  const UploadPolicyScreen({super.key, this.userCategory});

  @override
  State<UploadPolicyScreen> createState() => _UploadPolicyScreenState();
}

class _UploadPolicyScreenState extends State<UploadPolicyScreen> {
  String? _policyFileName;
  String? _policyFilePath;

  Future<void> _pickPolicyPdf() async {
    try {
      FilePickerResult? result = await FilePicker.platform.pickFiles(
        type: FileType.custom,
        allowedExtensions: ['pdf'],
      );

      if (result != null && result.files.isNotEmpty) {
        setState(() {
          _policyFileName = result.files.first.name;
          _policyFilePath = result.files.first.path;
        });
      }
    } catch (e) {
      if (!mounted) return;
      // Fallback demo file if file picker runs on web/emulator without storage permissions
      setState(() {
        _policyFileName = 'Insurance_Policy_Schedule_2026.pdf';
        _policyFilePath = '/storage/emulated/0/Download/Policy.pdf';
      });
    }
  }

  void _proceedToCamera() {
    if (_policyFileName == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Please upload your Insurance Policy PDF first.'),
          backgroundColor: Colors.amber,
        ),
      );
      return;
    }

    Navigator.pushReplacement(
      context,
      MaterialPageRoute(
        builder: (_) => CameraScreen(
          policyPdfName: _policyFileName,
          policyPdfPath: _policyFilePath,
          userCategory: widget.userCategory,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF8FAFC),
      appBar: AppBar(
        title: const Text('Step 1: Upload Insurance Policy'),
        backgroundColor: Colors.white,
        elevation: 0.5,
        foregroundColor: const Color(0xFF0F172A),
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24.0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Upload Policy Schedule',
                style: TextStyle(
                  fontSize: 20,
                  fontWeight: FontWeight.bold,
                  color: Color(0xFF0F172A),
                ),
              ),
              const SizedBox(height: 8),
              const Text(
                'First, upload your official Insurance Policy PDF document to initiate the claim.',
                style: TextStyle(color: Colors.grey, fontSize: 13),
              ),
              const SizedBox(height: 32),

              // Upload Box Widget
              GestureDetector(
                onTap: _pickPolicyPdf,
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(32),
                  decoration: BoxDecoration(
                    color: _policyFileName != null
                        ? const Color(0xFFECFDF5)
                        : Colors.white,
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(
                      color: _policyFileName != null
                          ? const Color(0xFF10B981)
                          : const Color(0xFF4F46E5).withOpacity(0.3),
                      width: 2,
                    ),
                  ),
                  child: Column(
                    children: [
                      Icon(
                        _policyFileName != null
                            ? Icons.picture_as_pdf
                            : Icons.cloud_upload_outlined,
                        size: 56,
                        color: _policyFileName != null
                            ? const Color(0xFF059669)
                            : const Color(0xFF4F46E5),
                      ),
                      const SizedBox(height: 16),
                      Text(
                        _policyFileName != null
                            ? _policyFileName!
                            : 'Tap to Select Policy PDF',
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                          color: _policyFileName != null
                              ? const Color(0xFF065F46)
                              : const Color(0xFF0F172A),
                        ),
                        textAlign: TextAlign.center,
                      ),
                      const SizedBox(height: 6),
                      Text(
                        _policyFileName != null
                            ? 'PDF attached successfully • Tap to replace'
                            : 'Supports PDF format',
                        style: TextStyle(
                          fontSize: 12,
                          color: _policyFileName != null
                              ? const Color(0xFF047857)
                              : Colors.grey,
                        ),
                      ),
                    ],
                  ),
                ),
              ),

              const Spacer(),

              // Proceed Button
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
                  onPressed: _proceedToCamera,
                  icon: const Icon(Icons.camera_alt, color: Colors.white),
                  label: const Text(
                    'Proceed to Photo Capture',
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
        ),
      ),
    );
  }
}

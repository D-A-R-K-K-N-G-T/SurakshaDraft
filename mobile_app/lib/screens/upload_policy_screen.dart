import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'camera_screen.dart';
import 'claim_form_screen.dart' show resolvePickedFilePath;

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
        withData: true, // ensure bytes exist if the picker returns no path
      );

      if (result != null && result.files.isNotEmpty) {
        final f = result.files.first;
        final path = await resolvePickedFilePath(f);
        if (path == null) {
          if (!mounted) return;
          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('Could not read that file. Please pick the policy PDF from local device storage.'),
            backgroundColor: Colors.amber,
          ));
          return;
        }
        setState(() {
          _policyFileName = f.name;
          _policyFilePath = path;
        });
      }
    } catch (e) {
      if (!mounted) return;
      // Do NOT fabricate a filename/path here. A nonexistent path makes the
      // upload throw at submit time, so the claim would silently proceed with
      // NO policy document while the UI claimed one was attached.
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not pick the policy PDF: $e')),
      );
    }
  }

  void _proceedToCamera() async {
    if (_policyFileName == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Please upload your Insurance Policy PDF first.'),
          backgroundColor: Colors.amber,
        ),
      );
      return;
    }

    final result = await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => CameraScreen(
          policyPdfName: _policyFileName,
          policyPdfPath: _policyFilePath,
          userCategory: widget.userCategory,
        ),
      ),
    );
    if (!mounted) return;
    Navigator.pop(context, result);
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

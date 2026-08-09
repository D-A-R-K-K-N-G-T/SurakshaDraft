import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:geolocator/geolocator.dart';
import 'package:intl/intl.dart';
import 'preview_confirm_screen.dart';

class CameraScreen extends StatefulWidget {
  final String? policyPdfName;
  final String? policyPdfPath;
  final String? userCategory;

  const CameraScreen({
    super.key,
    this.policyPdfName,
    this.policyPdfPath,
    this.userCategory,
  });

  @override
  State<CameraScreen> createState() => _CameraScreenState();
}

class _CameraScreenState extends State<CameraScreen> {
  final ImagePicker _picker = ImagePicker();
  String _statusMessage = 'Initializing Camera & Location...';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _checkLocationAndOpenCamera();
    });
  }

  Future<void> _checkLocationAndOpenCamera() async {
    setState(() {
      _statusMessage = 'Checking Location Permissions...';
    });

    try {
      // 1. Check GPS Location service
      bool serviceEnabled = await Geolocator.isLocationServiceEnabled();
      if (!serviceEnabled) {
        if (!mounted) return;
        _showLocationDialog(
          title: 'Location Services Disabled',
          content: 'Please turn on GPS / Location Services to geotag captured photo evidence.',
          onPressed: () async {
            Navigator.pop(context);
            await Geolocator.openLocationSettings();
            _checkLocationAndOpenCamera();
          },
        );
        return;
      }

      // 2. Check & Request permission
      LocationPermission permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
        if (permission == LocationPermission.denied) {
          if (!mounted) return;
          _showLocationDialog(
            title: 'Location Permission Denied',
            content: 'Location permission is required for timestamp and geotagging.',
            onPressed: () {
              Navigator.pop(context);
              Geolocator.requestPermission().then((_) => _checkLocationAndOpenCamera());
            },
          );
          return;
        }
      }

      if (permission == LocationPermission.deniedForever) {
        if (!mounted) return;
        _showLocationDialog(
          title: 'Location Permission Permanently Denied',
          content: 'Please grant location permission in App Settings to proceed.',
          onPressed: () {
            Navigator.pop(context);
            Geolocator.openAppSettings();
          },
        );
        return;
      }

      // 3. Open Camera
      setState(() => _statusMessage = 'Opening Camera...');
      XFile? photo;
      try {
        photo = await _picker.pickImage(
          source: ImageSource.camera,
        );
      } catch (e) {
        debugPrint('Camera pick error: $e');
      }

      if (photo == null) {
        if (!mounted) return;
        Navigator.pop(context);
        return;
      }

      // 4. Geotag & Timestamp (NO AI verification!)
      setState(() => _statusMessage = 'Geotagging & Timestamping Evidence...');

      Position currentPosition = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.high,
          timeLimit: Duration(seconds: 10),
        ),
      ).catchError((_) async {
        return await Geolocator.getLastKnownPosition() ??
            Position(
              longitude: 80.2341,
              latitude: 13.0418,
              timestamp: DateTime.now(),
              accuracy: 10.0,
              altitude: 0.0,
              heading: 0.0,
              speed: 0.0,
              speedAccuracy: 0.0,
              altitudeAccuracy: 0.0,
              headingAccuracy: 0.0,
            );
      });

      final now = DateTime.now();
      final formattedTimestamp = DateFormat('yyyy-MM-dd HH:mm:ss').format(now);
      final latLngString =
          '${currentPosition.latitude.toStringAsFixed(5)}° N, ${currentPosition.longitude.toStringAsFixed(5)}° E';

      if (!mounted) return;

      // Transition to the preview/confirm step (AI proposes items to confirm),
      // which then continues to the claim form.
      final result = await Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => PreviewConfirmScreen(
            policyPdfName: widget.policyPdfName,
            policyPdfPath: widget.policyPdfPath,
            photoPath: photo!.path,
            geotag: latLngString,
            timestamp: formattedTimestamp,
            // Raw values for the backend (the display strings above are for UI).
            photoLat: currentPosition.latitude,
            photoLon: currentPosition.longitude,
            photoCapturedAt: now.toUtc().toIso8601String(),
            userCategory: widget.userCategory,
          ),
        ),
      );
      if (!mounted) return;
      Navigator.pop(context, result);
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Camera / Geotag Error: $e')),
      );
    }
  }

  void _showLocationDialog({
    required String title,
    required String content,
    required VoidCallback onPressed,
  }) {
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        title: Row(
          children: [
            const Icon(Icons.location_off, color: Colors.redAccent),
            const SizedBox(width: 8),
            Expanded(child: Text(title, style: const TextStyle(fontSize: 16))),
          ],
        ),
        content: Text(content),
        actions: [
          TextButton(
            onPressed: () {
              Navigator.pop(ctx);
              Navigator.pop(context);
            },
            child: const Text('Cancel'),
          ),
          ElevatedButton(
            onPressed: onPressed,
            style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF4F46E5)),
            child: const Text('Turn On / Grant', style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: SafeArea(
        child: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const CircularProgressIndicator(color: Color(0xFF4F46E5)),
              const SizedBox(height: 24),
              Text(
                _statusMessage,
                style: const TextStyle(color: Colors.white, fontSize: 16),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 32),
              OutlinedButton.icon(
                onPressed: _checkLocationAndOpenCamera,
                icon: const Icon(Icons.camera_alt, color: Colors.white),
                label: const Text('Re-open Camera', style: TextStyle(color: Colors.white)),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

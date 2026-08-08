import 'dart:math';
import 'package:flutter/material.dart';
import '../models/line_item.dart';
import 'review_form_screen.dart';

class ProcessingScreen extends StatefulWidget {
  final String? capturedPhotoPath;
  final String geotag;
  final String timestamp;
  final String imageHash;

  const ProcessingScreen({
    super.key,
    this.capturedPhotoPath,
    this.geotag = '13.0418° N, 80.2341° E',
    this.timestamp = '2026-08-08 19:35:00',
    this.imageHash = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
  });

  @override
  State<ProcessingScreen> createState() => _ProcessingScreenState();
}

class _ProcessingScreenState extends State<ProcessingScreen>
    with SingleTickerProviderStateMixin {
  late AnimationController _animController;
  String _statusText = 'Identifying item…';
  double _progress = 0.2;

  @override
  void initState() {
    super.initState();
    _animController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1500),
    )..repeat(reverse: true);

    _startSimulatedAiPipeline();
  }

  void _startSimulatedAiPipeline() async {
    await Future.delayed(const Duration(milliseconds: 600));
    if (!mounted) return;
    setState(() {
      _statusText = 'Extracting geotag & timestamp proof…';
      _progress = 0.45;
    });

    await Future.delayed(const Duration(milliseconds: 700));
    if (!mounted) return;
    setState(() {
      _statusText = 'Classifying line item & damage status…';
      _progress = 0.80;
    });

    await Future.delayed(const Duration(milliseconds: 700));
    if (!mounted) return;
    setState(() {
      _statusText = 'Draft line item generated!';
      _progress = 1.0;
    });

    await Future.delayed(const Duration(milliseconds: 300));
    if (!mounted) return;

    final randomId = 'LI-${Random().nextInt(900) + 100}';
    final shortHash = widget.imageHash.length > 12
        ? widget.imageHash.substring(0, 12)
        : widget.imageHash;

    final generatedItem = LineItem(
      id: randomId,
      name: 'Captured Inventory Item',
      description:
          'Geotagged at ${widget.geotag} on ${widget.timestamp}. Visual damage analysis pending.',
      category: 'Stock',
      quantity: 1,
      unit: 'pcs',
      estimatedValue: 12500,
      status: ItemStatus.confirmed,
      confidenceScore: 0.94,
      imagePath: widget.capturedPhotoPath,
      valueSource: 'Geotag: ${widget.geotag} | Time: ${widget.timestamp}',
      evidenceRefs: [
        'IMG-${shortHash.toUpperCase()}',
        'GPS-${widget.geotag}',
      ],
    );

    Navigator.pushReplacement(
      context,
      MaterialPageRoute(
        builder: (_) => ReviewFormScreen(draftItem: generatedItem),
      ),
    );
  }

  @override
  void dispose() {
    _animController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0F172A),
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(32.0),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                ScaleTransition(
                  scale: Tween<double>(begin: 0.9, end: 1.1).animate(
                    CurvedAnimation(
                      parent: _animController,
                      curve: Curves.easeInOut,
                    ),
                  ),
                  child: Container(
                    width: 110,
                    height: 110,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      gradient: const RadialGradient(
                        colors: [Color(0xFF6366F1), Color(0xFF312E81)],
                      ),
                      boxShadow: [
                        BoxShadow(
                          color: const Color(0xFF6366F1).withOpacity(0.5),
                          blurRadius: 30,
                          spreadRadius: 10,
                        ),
                      ],
                    ),
                    child: const Icon(
                      Icons.auto_awesome,
                      size: 50,
                      color: Colors.cyanAccent,
                    ),
                  ),
                ),
                const SizedBox(height: 36),

                Text(
                  _statusText,
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 18,
                    fontWeight: FontWeight.bold,
                  ),
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 8),
                Text(
                  '📍 ${widget.geotag}\n🕒 ${widget.timestamp}',
                  style: const TextStyle(color: Colors.cyanAccent, fontSize: 12),
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 32),

                ClipRRect(
                  borderRadius: BorderRadius.circular(10),
                  child: LinearProgressIndicator(
                    value: _progress,
                    minHeight: 8,
                    backgroundColor: Colors.white10,
                    valueColor: const AlwaysStoppedAnimation<Color>(
                      Color(0xFF22D3EE),
                    ),
                  ),
                ),
                const SizedBox(height: 16),

                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    const Icon(Icons.security, color: Colors.greenAccent, size: 14),
                    const SizedBox(width: 6),
                    Flexible(
                      child: Text(
                        'Hash: ${widget.imageHash}',
                        style: TextStyle(
                          color: Colors.white.withOpacity(0.4),
                          fontSize: 9,
                          fontFamily: 'monospace',
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

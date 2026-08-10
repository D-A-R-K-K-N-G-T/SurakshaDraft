import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import '../config.dart';

class FirmDashboardScreen extends StatefulWidget {
  final String firmName;
  const FirmDashboardScreen({super.key, required this.firmName});

  @override
  State<FirmDashboardScreen> createState() => _FirmDashboardScreenState();
}

class _FirmDashboardScreenState extends State<FirmDashboardScreen> {
  bool _isLoading = true;
  List<dynamic> _claims = [];
  String? _error;

  @override
  void initState() {
    super.initState();
    _fetchClaims();
  }

  Future<void> _fetchClaims() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });
    try {
      final response = await http.get(
        Uri.parse('$kApiBase/api/firm/${widget.firmName}/claims'),
      );
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        setState(() {
          _claims = data['claims'] ?? [];
        });
      } else {
        setState(() {
          _error = 'Failed to load claims. Code: ${response.statusCode}';
        });
      }
    } catch (e) {
      setState(() {
        _error = 'Error fetching claims: $e';
      });
    } finally {
      if (mounted) {
        setState(() {
          _isLoading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('${widget.firmName.toUpperCase()} Dashboard'),
        backgroundColor: const Color(0xFF1E1B4B),
        foregroundColor: Colors.white,
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _fetchClaims,
          ),
        ],
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_isLoading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.error_outline, size: 48, color: Colors.red),
            const SizedBox(height: 16),
            Text(_error!, style: const TextStyle(color: Colors.red)),
            TextButton(onPressed: _fetchClaims, child: const Text('Retry')),
          ],
        ),
      );
    }
    if (_claims.isEmpty) {
      return const Center(child: Text('No claims found for this firm.'));
    }

    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: _claims.length,
      itemBuilder: (context, index) {
        final claim = _claims[index];
        final ref = claim['claim_ref'] ?? 'Unknown';
        final status = claim['status'] ?? 'processing';
        final desc = claim['event_description'] ?? 'No description';
        final dateStr = claim['created_at'];
        
        // Extract analysis details from latest_state
        final state = claim['latest_state'] ?? {};
        final items = state['line_items'] ?? [];
        final anomalies = state['anomalies'] ?? [];
        
        return Card(
          elevation: 2,
          margin: const EdgeInsets.only(bottom: 16),
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      'Claim $ref',
                      style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
                    ),
                    Chip(
                      label: Text(status.toUpperCase()),
                      backgroundColor: status == 'processing' ? Colors.blue.shade100 : 
                                       status == 'completed' ? Colors.green.shade100 : Colors.grey.shade200,
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Text(desc, style: const TextStyle(color: Colors.black87)),
                if (dateStr != null) ...[
                  const SizedBox(height: 4),
                  Text('Date: $dateStr', style: const TextStyle(color: Colors.black54, fontSize: 12)),
                ],
                const Divider(height: 24),
                const Text('AI Analysis:', style: TextStyle(fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                Text('Items Detected: ${items.length}'),
                if (anomalies.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  const Text('Anomalies:', style: TextStyle(color: Colors.red, fontWeight: FontWeight.bold)),
                  for (var anomaly in anomalies)
                    Text('- $anomaly', style: const TextStyle(color: Colors.red, fontSize: 13)),
                ],
              ],
            ),
          ),
        );
      },
    );
  }
}

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:intl/intl.dart';
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:flutter_markdown/flutter_markdown.dart';
import '../models/claim_model.dart';
import 'upload_policy_screen.dart';

class ItemListScreen extends StatefulWidget {
  const ItemListScreen({super.key});

  @override
  State<ItemListScreen> createState() => _ItemListScreenState();
}

class _ItemListScreenState extends State<ItemListScreen> {
  List<ClaimRecord> _claims = [];
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
    setState(() {
      _userCategory = prefs.getString('user_category') ?? 'Personal';
      _businessName = prefs.getString('policy_business_name') ?? 'My Claims Dashboard';
      _policyNumber = prefs.getString('policy_number') ?? 'POL-ACTIVE';
      _insurerName = prefs.getString('policy_insurer') ?? 'Insurance Company';

      if (_userCategory == 'Insurance Firm' && _businessName.contains('ABC')) {
        _claims = [
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
        ];
      }
    });
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

  Future<void> _pollClaimStatus(String claimId) async {
    const int maxAttempts = 40;
    int attempts = 0;

    while (attempts < maxAttempts) {
      await Future.delayed(const Duration(seconds: 3));
      if (!mounted) return;

      try {
        final response = await http.get(Uri.parse('http://10.0.2.2:3000/api/claim/$claimId'));
        if (response.statusCode == 200) {
          final data = jsonDecode(response.body);
          if (data['status'] == 'completed') {
            final draftPack = data['state']['draft_pack'];
            final index = _claims.indexWhere((c) => c.id == claimId);
            if (index != -1 && _claims[index].status == ClaimStatus.pending) {
              setState(() {
                _claims[index] = _claims[index].copyWith(
                  status: ClaimStatus.review,
                  draftPackSummary: 'DRAFT PACK GENERATED:\n\n### Main Schedule\n${draftPack['main_schedule']}\n\n### Rejected Items\n${draftPack['rejected_items_annexure']}',
                  aiReasoning: 'AI completed analysis and generated the draft pack.',
                  policyStatusText: 'Status updated by AI',
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
          } else if (data['status'] == 'failed') {
            debugPrint('Pipeline failed: ${data['error']}');
            final index = _claims.indexWhere((c) => c.id == claimId);
            if (index != -1 && _claims[index].status == ClaimStatus.pending) {
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

  List<ClaimRecord> get _filteredClaims {
    if (_selectedFilter == 'All') return _claims;
    if (_selectedFilter == 'Submitted') {
      return _claims.where((c) => c.status == ClaimStatus.submitted).toList();
    }
    if (_selectedFilter == 'Pending') {
      return _claims.where((c) => c.status == ClaimStatus.pending).toList();
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

              // Coverage Status Badge
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: const Color(0xFFECFDF5),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: const Color(0xFF10B981)),
                ),
                child: const Row(
                  children: [
                    Icon(Icons.check_circle, color: Color(0xFF059669), size: 22),
                    SizedBox(width: 10),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'Policy Status: COVERED',
                            style: TextStyle(
                              fontWeight: FontWeight.bold,
                              fontSize: 14,
                              color: Color(0xFF065F46),
                            ),
                          ),
                          Text(
                            'Confirmed covered under active insurance policy schedule.',
                            style: TextStyle(fontSize: 12, color: Color(0xFF047857)),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
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
                          .where((c) => c.status == ClaimStatus.pending)
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
              child: _filteredClaims.isEmpty
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
                  : ListView.builder(
                      padding: const EdgeInsets.fromLTRB(16, 8, 16, 90),
                      itemCount: _filteredClaims.length,
                      itemBuilder: (context, index) {
                        final claim = _filteredClaims[index];
                        return _buildClaimCard(claim);
                      },
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
    }

    return GestureDetector(
      onTap: () {
        if (claim.status == ClaimStatus.review) {
          _showDraftPackReviewModal(claim);
        } else if (claim.status == ClaimStatus.pending) {
          _showPendingStatusModal(claim);
        } else {
          _showDraftPackReviewModal(claim);
        }
      },
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

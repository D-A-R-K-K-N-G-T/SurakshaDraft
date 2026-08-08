import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:intl/intl.dart';
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
              const Icon(Icons.check_circle, color: Colors.greenAccent),
              const SizedBox(width: 8),
              Expanded(
                child: Text('Claim "${newClaim.itemName}" submitted successfully!'),
              ),
            ],
          ),
          backgroundColor: const Color(0xFF0F172A),
          duration: const Duration(seconds: 3),
        ),
      );
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

            // Claims List or Empty State (No overlap!)
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
                      padding: const EdgeInsets.fromLTRB(16, 8, 16, 90), // Added bottom padding to avoid FAB overlap
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
        statusText = 'Pending';
        break;
      case ClaimStatus.review:
        statusBgColor = const Color(0xFFEFF6FF);
        statusTextColor = const Color(0xFF2563EB);
        statusText = 'Review';
        break;
    }

    return Container(
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
                Text(
                  claim.timestamp,
                  style: const TextStyle(fontSize: 10, color: Colors.grey),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

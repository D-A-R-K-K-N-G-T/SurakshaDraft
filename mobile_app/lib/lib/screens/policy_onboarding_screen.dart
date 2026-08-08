import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'item_list_screen.dart';

class PolicyOnboardingScreen extends StatefulWidget {
  const PolicyOnboardingScreen({super.key});

  @override
  State<PolicyOnboardingScreen> createState() => _PolicyOnboardingScreenState();
}

class _PolicyOnboardingScreenState extends State<PolicyOnboardingScreen> {
  final _formKey = GlobalKey<FormState>();
  final _businessNameController = TextEditingController(); // Name for Personal, Business Name for Commercial
  final _policyNumberController = TextEditingController();
  final _insurerController = TextEditingController();
  final _sumInsuredController = TextEditingController();
  final _excessController = TextEditingController();

  String _userCategory = 'Personal';
  bool _isSaving = false;

  @override
  void initState() {
    super.initState();
    _loadUserCategory();
  }

  Future<void> _loadUserCategory() async {
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      _userCategory = prefs.getString('user_category') ?? 'Personal';
    });
  }

  @override
  void dispose() {
    _businessNameController.dispose();
    _policyNumberController.dispose();
    _insurerController.dispose();
    _sumInsuredController.dispose();
    _excessController.dispose();
    super.dispose();
  }

  Future<void> _savePolicyAndProceed() async {
    if (!_formKey.currentState!.validate()) return;

    setState(() => _isSaving = true);

    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('policy_business_name', _businessNameController.text.trim());
    await prefs.setString('policy_insurer', _insurerController.text.trim());

    if (_userCategory != 'Personal') {
      await prefs.setString('policy_number', _policyNumberController.text.trim());
      await prefs.setDouble('policy_sum_insured', double.tryParse(_sumInsuredController.text) ?? 0);
      await prefs.setDouble('policy_excess', double.tryParse(_excessController.text) ?? 0);
    } else {
      await prefs.setString('policy_number', 'POL-PERSONAL');
    }

    await prefs.setBool('policy_setup_complete', true);

    if (!mounted) return;

    Navigator.of(context).pushAndRemoveUntil(
      MaterialPageRoute(builder: (_) => const ItemListScreen()),
      (route) => false,
    );
  }

  @override
  Widget build(BuildContext context) {
    final isPersonal = _userCategory == 'Personal';

    return Scaffold(
      backgroundColor: const Color(0xFFF8FAFC),
      appBar: AppBar(
        title: Text(isPersonal ? 'Setup Personal Profile' : 'Phase A: Policy Setup'),
        backgroundColor: Colors.white,
        elevation: 0.5,
        foregroundColor: const Color(0xFF0F172A),
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24.0),
          child: Form(
            key: _formKey,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Header Banner
                Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: const Color(0xFFEEF2FF),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: const Color(0xFFC7D2FE)),
                  ),
                  child: Row(
                    children: [
                      Icon(
                        isPersonal ? Icons.person_outline : Icons.shield_outlined,
                        color: const Color(0xFF4F46E5),
                        size: 28,
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              isPersonal ? 'Setup Personal Details' : 'Setup Business Policy',
                              style: const TextStyle(
                                fontWeight: FontWeight.bold,
                                color: Color(0xFF1E1B4B),
                                fontSize: 14,
                              ),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              isPersonal
                                  ? 'Enter your name and insurance provider to file personal claims.'
                                  : 'Capture policy schedule details once before logging claim items.',
                              style: const TextStyle(fontSize: 12, color: Color(0xFF4338CA)),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 24),

                // Form Fields
                if (isPersonal) ...[
                  // Personal Profile: Name & Insurance Firm Name ONLY
                  _buildInputField(
                    controller: _businessNameController,
                    label: 'Full Name',
                    hint: 'e.g. Rahul Sharma',
                    icon: Icons.person_outline,
                    validator: (val) => val == null || val.trim().isEmpty
                        ? 'Please enter your full name'
                        : null,
                  ),
                  const SizedBox(height: 16),
                  _buildInputField(
                    controller: _insurerController,
                    label: 'Insurance Firm Name',
                    hint: 'e.g. HDFC ERGO / Star Health / ICICI Lombard',
                    icon: Icons.verified_user_outlined,
                    validator: (val) => val == null || val.trim().isEmpty
                        ? 'Please enter insurance firm name'
                        : null,
                  ),
                ] else ...[
                  // Commercial / Insurance Firm Setup
                  _buildInputField(
                    controller: _businessNameController,
                    label: 'Business / Store Name',
                    hint: 'e.g. Apex Enterprises',
                    icon: Icons.store,
                    validator: (val) => val == null || val.trim().isEmpty
                        ? 'Please enter your business name'
                        : null,
                  ),
                  const SizedBox(height: 16),

                  _buildInputField(
                    controller: _policyNumberController,
                    label: 'Policy Schedule Number',
                    hint: 'e.g. POL-2026-88192',
                    icon: Icons.numbers,
                    validator: (val) => val == null || val.trim().isEmpty
                        ? 'Please enter policy number'
                        : null,
                  ),
                  const SizedBox(height: 16),

                  _buildInputField(
                    controller: _insurerController,
                    label: 'Insurer Name',
                    hint: 'e.g. New India Assurance',
                    icon: Icons.verified_user,
                    validator: (val) => val == null || val.trim().isEmpty
                        ? 'Please enter insurer name'
                        : null,
                  ),
                  const SizedBox(height: 16),

                  Row(
                    children: [
                      Expanded(
                        child: _buildInputField(
                          controller: _sumInsuredController,
                          label: 'Stock Sum Insured (₹)',
                          icon: Icons.currency_rupee,
                          keyboardType: TextInputType.number,
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: _buildInputField(
                          controller: _excessController,
                          label: 'Policy Excess (₹)',
                          icon: Icons.remove_circle_outline,
                          keyboardType: TextInputType.number,
                        ),
                      ),
                    ],
                  ),
                ],

                const SizedBox(height: 32),

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
                    onPressed: _isSaving ? null : _savePolicyAndProceed,
                    icon: _isSaving
                        ? const SizedBox(
                            width: 20,
                            height: 20,
                            child: CircularProgressIndicator(
                              color: Colors.white,
                              strokeWidth: 2,
                            ),
                          )
                        : const Icon(Icons.check_circle_outline, color: Colors.white),
                    label: Text(
                      _isSaving ? 'Saving...' : 'Confirm Details & Open Hub',
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
    TextInputType keyboardType = TextInputType.text,
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
        keyboardType: keyboardType,
        validator: validator,
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          labelStyle: TextStyle(color: Colors.grey.shade700, fontSize: 13),
          prefixIcon: Icon(icon, color: const Color(0xFF4F46E5), size: 20),
          border: InputBorder.none,
          contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        ),
      ),
    );
  }
}

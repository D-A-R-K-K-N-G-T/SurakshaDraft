import 'package:flutter/material.dart';
import '../models/line_item.dart';

class ReserveSummarySheet extends StatelessWidget {
  final List<LineItem> items;

  const ReserveSummarySheet({super.key, required this.items});

  @override
  Widget build(BuildContext context) {
    double confirmedTotal = 0;
    double conditionalTotal = 0;
    double pendingTotal = 0;
    double screenedOutTotal = 0;

    for (var item in items) {
      switch (item.status) {
        case ItemStatus.confirmed:
          confirmedTotal += item.estimatedValue;
          break;
        case ItemStatus.needsReview:
          conditionalTotal += item.estimatedValue;
          break;
        case ItemStatus.pendingVerification:
          pendingTotal += item.estimatedValue;
          break;
        case ItemStatus.rejected:
          screenedOutTotal += item.estimatedValue;
          break;
      }
    }

    double netLoss = confirmedTotal + conditionalTotal + pendingTotal;

    return Container(
      padding: const EdgeInsets.all(24),
      decoration: const BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
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
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Phase I: Reserve Estimate',
                      style: TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.bold,
                        color: Color(0xFF1E293B),
                      ),
                    ),
                    Text(
                      'Sri Lakshmi Textiles • Flood Claim',
                      style: TextStyle(fontSize: 13, color: Colors.grey),
                    ),
                  ],
                ),
                IconButton(
                  icon: const Icon(Icons.close),
                  onPressed: () => Navigator.pop(context),
                ),
              ],
            ),
            const Divider(height: 32),

            // Main Net Total Card
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                gradient: const LinearGradient(
                  colors: [Color(0xFF4F46E5), Color(0xFF6366F1)],
                ),
                borderRadius: BorderRadius.circular(16),
              ),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  const Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Total Claim Reserve',
                        style: TextStyle(color: Colors.white70, fontSize: 13),
                      ),
                      SizedBox(height: 4),
                      Text(
                        'Transparent Loss Breakdown',
                        style: TextStyle(color: Colors.white, fontSize: 11),
                      ),
                    ],
                  ),
                  Text(
                    '₹${netLoss.toStringAsFixed(0)}',
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 24,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            _buildCategoryRow(
              title: 'Confirmed Loss',
              basis: 'Receipt-backed, covered & verified',
              amount: confirmedTotal,
              color: Colors.green.shade700,
              badgeColor: Colors.green.shade50,
              icon: Icons.check_circle_outline,
            ),
            const SizedBox(height: 12),

            _buildCategoryRow(
              title: 'Conditional (Under Review)',
              basis: 'Valued, policy clause ambiguity (LI-5)',
              amount: conditionalTotal,
              color: Colors.amber.shade900,
              badgeColor: Colors.amber.shade50,
              icon: Icons.warning_amber_outlined,
            ),
            const SizedBox(height: 12),

            _buildCategoryRow(
              title: 'Pending Verification',
              basis: 'Washed away, paper records attached (LI-7)',
              amount: pendingTotal,
              color: Colors.blue.shade800,
              badgeColor: Colors.blue.shade50,
              icon: Icons.pending_actions,
            ),
            const SizedBox(height: 12),

            _buildCategoryRow(
              title: 'Screened Out (Rejected)',
              basis: 'Duplicate serial SN-RP4471 (LI-4)',
              amount: screenedOutTotal,
              color: Colors.red.shade700,
              badgeColor: Colors.red.shade50,
              icon: Icons.cancel_outlined,
            ),

            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              height: 50,
              child: ElevatedButton.icon(
                style: ElevatedButton.styleFrom(
                  backgroundColor: const Color(0xFF4F46E5),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
                onPressed: () => Navigator.pop(context),
                icon: const Icon(Icons.arrow_back, color: Colors.white),
                label: const Text(
                  'Back to Item List',
                  style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w600),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildCategoryRow({
    required String title,
    required String basis,
    required double amount,
    required Color color,
    required Color badgeColor,
    required IconData icon,
  }) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: badgeColor,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withOpacity(0.3)),
      ),
      child: Row(
        children: [
          Icon(icon, color: color, size: 24),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: TextStyle(
                    fontWeight: FontWeight.bold,
                    color: color,
                    fontSize: 14,
                  ),
                ),
                Text(
                  basis,
                  style: TextStyle(color: Colors.grey.shade700, fontSize: 11),
                ),
              ],
            ),
          ),
          Text(
            '₹${amount.toStringAsFixed(0)}',
            style: TextStyle(
              fontWeight: FontWeight.bold,
              fontSize: 15,
              color: color,
            ),
          ),
        ],
      ),
    );
  }
}

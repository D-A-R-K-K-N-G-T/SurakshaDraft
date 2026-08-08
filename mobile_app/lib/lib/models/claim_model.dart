enum ClaimStatus {
  submitted,
  pending,
  review,
}

class ClaimRecord {
  final String id;
  final String itemName;
  final String category; // Personal, Commercial, Insurance Firm
  final String itemType; // Sub-category (e.g. Vehicle, Machinery, Stock)
  final String? policyPdfPath;
  final String? policyPdfName;
  final String? photoPath;
  final String geotag;
  final String timestamp;
  final String? govtIdPath;
  final String? govtIdName;
  final String permanentAddress;
  final DateTime lossDate;
  final String? businessType;
  final String? gstinNumber;
  final ClaimStatus status;

  ClaimRecord({
    required this.id,
    required this.itemName,
    required this.category,
    required this.itemType,
    this.policyPdfPath,
    this.policyPdfName,
    this.photoPath,
    required this.geotag,
    required this.timestamp,
    this.govtIdPath,
    this.govtIdName,
    required this.permanentAddress,
    required this.lossDate,
    this.businessType,
    this.gstinNumber,
    this.status = ClaimStatus.submitted,
  });
}

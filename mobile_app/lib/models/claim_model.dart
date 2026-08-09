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
  final String? draftPackSummary;
  final String? aiReasoning;
  final String? policyStatusText;
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
    this.draftPackSummary,
    this.aiReasoning,
    this.policyStatusText,
    this.status = ClaimStatus.pending,
  });

  ClaimRecord copyWith({
    String? id,
    String? itemName,
    String? category,
    String? itemType,
    String? policyPdfPath,
    String? policyPdfName,
    String? photoPath,
    String? geotag,
    String? timestamp,
    String? govtIdPath,
    String? govtIdName,
    String? permanentAddress,
    DateTime? lossDate,
    String? businessType,
    String? gstinNumber,
    String? draftPackSummary,
    String? aiReasoning,
    String? policyStatusText,
    ClaimStatus? status,
  }) {
    return ClaimRecord(
      id: id ?? this.id,
      itemName: itemName ?? this.itemName,
      category: category ?? this.category,
      itemType: itemType ?? this.itemType,
      policyPdfPath: policyPdfPath ?? this.policyPdfPath,
      policyPdfName: policyPdfName ?? this.policyPdfName,
      photoPath: photoPath ?? this.photoPath,
      geotag: geotag ?? this.geotag,
      timestamp: timestamp ?? this.timestamp,
      govtIdPath: govtIdPath ?? this.govtIdPath,
      govtIdName: govtIdName ?? this.govtIdName,
      permanentAddress: permanentAddress ?? this.permanentAddress,
      lossDate: lossDate ?? this.lossDate,
      businessType: businessType ?? this.businessType,
      gstinNumber: gstinNumber ?? this.gstinNumber,
      draftPackSummary: draftPackSummary ?? this.draftPackSummary,
      aiReasoning: aiReasoning ?? this.aiReasoning,
      policyStatusText: policyStatusText ?? this.policyStatusText,
      status: status ?? this.status,
    );
  }
}

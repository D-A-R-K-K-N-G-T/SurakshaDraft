enum ItemStatus {
  confirmed,
  needsReview,
  pendingVerification,
  rejected,
}

class LineItem {
  final String id;
  final String name;
  final String description;
  final String category; // e.g. Stock, Machinery, FFF, Electronics
  final double quantity;
  final String unit;
  final double estimatedValue;
  final ItemStatus status;
  final double confidenceScore; // 0.0 to 1.0
  final String? serialNumber;
  final List<String> reasons;
  final List<String> evidenceRefs;
  final String? imagePath;
  final String? valueSource;

  LineItem({
    required this.id,
    required this.name,
    required this.description,
    required this.category,
    required this.quantity,
    this.unit = 'pcs',
    required this.estimatedValue,
    required this.status,
    this.confidenceScore = 0.95,
    this.serialNumber,
    this.reasons = const [],
    this.evidenceRefs = const [],
    this.imagePath,
    this.valueSource,
  });

  bool get isAiConfident => confidenceScore >= 0.80;

  LineItem copyWith({
    String? id,
    String? name,
    String? description,
    String? category,
    double? quantity,
    String? unit,
    double? estimatedValue,
    ItemStatus? status,
    double? confidenceScore,
    String? serialNumber,
    List<String>? reasons,
    List<String>? evidenceRefs,
    String? imagePath,
    String? valueSource,
  }) {
    return LineItem(
      id: id ?? this.id,
      name: name ?? this.name,
      description: description ?? this.description,
      category: category ?? this.category,
      quantity: quantity ?? this.quantity,
      unit: unit ?? this.unit,
      estimatedValue: estimatedValue ?? this.estimatedValue,
      status: status ?? this.status,
      confidenceScore: confidenceScore ?? this.confidenceScore,
      serialNumber: serialNumber ?? this.serialNumber,
      reasons: reasons ?? this.reasons,
      evidenceRefs: evidenceRefs ?? this.evidenceRefs,
      imagePath: imagePath ?? this.imagePath,
      valueSource: valueSource ?? this.valueSource,
    );
  }
}

/// The Letter of Requirement: the claimant's document checklist.
///
/// Mirrors LORPack / RequirementResult in agentic_pipeline/schemas.py. Two
/// revisions arrive per claim: revision 1 comes straight back in the submit
/// response and covers only the documents every claim needs (the claim type is
/// not known yet), then revision 2+ arrives via polling once the pipeline has
/// identified the claim type and read the uploaded files.
library;

enum RequirementStatus { satisfied, unverified, missing }

enum RequirementSeverity { blocking, advisory }

/// How a requirement gets ticked off.
/// - [classified]: the pipeline independently identified the file. "Verified".
/// - [attested]: the user uploaded something against this row and we make no
///   claim about what it is. Must never be presented as verified.
enum RequirementVerification { classified, attested }

T _enumFrom<T>(List<T> values, String? raw, T fallback) {
  if (raw == null) return fallback;
  for (final v in values) {
    if (v.toString().split('.').last == raw) return v;
  }
  return fallback;
}

class RequirementResult {
  final String requirementId;
  final String label;
  final String helpText;
  final RequirementStatus status;
  final RequirementSeverity severity;
  final RequirementVerification verification;
  final String message;
  final List<String> satisfiedBy;

  const RequirementResult({
    required this.requirementId,
    required this.label,
    this.helpText = '',
    required this.status,
    required this.severity,
    required this.verification,
    this.message = '',
    this.satisfiedBy = const [],
  });

  bool get isBlocking => severity == RequirementSeverity.blocking;

  /// True when this row was ticked without us checking the contents.
  bool get isUnverifiedTick =>
      status == RequirementStatus.satisfied &&
      verification == RequirementVerification.attested;

  factory RequirementResult.fromJson(Map<String, dynamic> json) {
    return RequirementResult(
      requirementId: json['requirement_id']?.toString() ?? '',
      label: json['label']?.toString() ?? '',
      helpText: json['help_text']?.toString() ?? '',
      status: _enumFrom(RequirementStatus.values, json['status']?.toString(),
          RequirementStatus.missing),
      severity: _enumFrom(RequirementSeverity.values, json['severity']?.toString(),
          RequirementSeverity.advisory),
      verification: _enumFrom(RequirementVerification.values,
          json['verification']?.toString(), RequirementVerification.attested),
      message: json['message']?.toString() ?? '',
      satisfiedBy: ((json['satisfied_by'] as List?) ?? const [])
          .map((e) => e.toString())
          .toList(),
    );
  }
}

class LorPack {
  final int revision;

  /// "universal_only" for revision 1 (claim type not yet known), or
  /// "claim_type_narrowed" once the pipeline has classified the claim.
  final String basis;
  final String? claimType;
  final String? claimTypeLabel;
  final double? claimTypeConfidence;

  /// The pipeline could not settle on one claim type, so this list is the union
  /// of the possibilities and nothing claim-type-specific is holding the claim.
  final bool claimTypeAmbiguous;
  final String rulesetId;
  final List<RequirementResult> satisfied;
  final List<RequirementResult> unverified;
  final List<RequirementResult> missing;
  final List<String> blockingMissing;
  final List<String> notes;

  const LorPack({
    this.revision = 1,
    this.basis = 'universal_only',
    this.claimType,
    this.claimTypeLabel,
    this.claimTypeConfidence,
    this.claimTypeAmbiguous = false,
    this.rulesetId = '',
    this.satisfied = const [],
    this.unverified = const [],
    this.missing = const [],
    this.blockingMissing = const [],
    this.notes = const [],
  });

  /// Revision 1 is built before the claim type is known, so more rows may still
  /// be added. The UI must not present it as the final list.
  bool get isProvisional => basis == 'universal_only';

  bool get hasBlockingGaps => blockingMissing.isNotEmpty;

  /// Everything still outstanding, blocking rows first.
  List<RequirementResult> get outstanding {
    final rows = [...missing, ...unverified];
    rows.sort((a, b) {
      if (a.isBlocking == b.isBlocking) return 0;
      return a.isBlocking ? -1 : 1;
    });
    return rows;
  }

  int get totalCount => satisfied.length + unverified.length + missing.length;

  static List<RequirementResult> _rows(dynamic raw) =>
      ((raw as List?) ?? const [])
          .map((e) => RequirementResult.fromJson(Map<String, dynamic>.from(e)))
          .toList();

  factory LorPack.fromJson(Map<String, dynamic> json) {
    return LorPack(
      revision: (json['revision'] as num?)?.toInt() ?? 1,
      basis: json['basis']?.toString() ?? 'universal_only',
      claimType: json['claim_type']?.toString(),
      claimTypeLabel: json['claim_type_label']?.toString(),
      claimTypeConfidence: (json['claim_type_confidence'] as num?)?.toDouble(),
      claimTypeAmbiguous: json['claim_type_ambiguous'] == true,
      rulesetId: json['ruleset_id']?.toString() ?? '',
      satisfied: _rows(json['satisfied']),
      unverified: _rows(json['unverified']),
      missing: _rows(json['missing']),
      blockingMissing: ((json['blocking_missing'] as List?) ?? const [])
          .map((e) => e.toString())
          .toList(),
      notes: ((json['notes'] as List?) ?? const [])
          .map((e) => e.toString())
          .toList(),
    );
  }

  /// Tolerant parse for anything that may or may not carry a pack.
  static LorPack? tryFrom(dynamic raw) {
    if (raw is! Map) return null;
    try {
      return LorPack.fromJson(Map<String, dynamic>.from(raw));
    } catch (_) {
      return null;
    }
  }
}

/// One selectable claim type, read back from the insurer's ruleset. Populates
/// the "not the right claim type?" picker.
class ClaimTypeOption {
  final String id;
  final String label;
  final String description;

  const ClaimTypeOption({
    required this.id,
    required this.label,
    this.description = '',
  });

  factory ClaimTypeOption.fromJson(Map<String, dynamic> json) => ClaimTypeOption(
        id: json['id']?.toString() ?? '',
        label: json['label']?.toString() ?? '',
        description: json['description']?.toString() ?? '',
      );
}

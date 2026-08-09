import 'dart:convert';
import 'dart:io';
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Offline claim drafts.
///
/// When the user is signed in but has no internet, they can still capture
/// timestamped, geotagged photos (and enter what they know). We persist that as
/// a local DRAFT — metadata in SharedPreferences, evidence/document files copied
/// into the app's documents dir so they survive an app restart — and show it on
/// the dashboard. When back online they resume the draft in the claim form and
/// submit; the draft is deleted on a successful submit.
const String _kDraftsKey = 'offline_drafts_v1';

class DraftClaim {
  final String id;
  final String createdAt; // ISO
  final String? userCategory;
  // captured evidence
  final String? photoPath;
  final String geotag;
  final String timestamp; // display string shown on the card / carried to the form
  final double? photoLat;
  final double? photoLon;
  final String? photoCapturedAt; // ISO (UTC)
  // documents (may be added before or after going offline)
  final String? policyPdfPath;
  final String? policyPdfName;
  final String? govtIdPath;
  final String? govtIdName;
  final String? invoicePath;
  final String? invoiceName;
  // partial form fields
  final String itemName;
  final String? itemType;
  final String permanentAddress;
  final String? businessType;
  final String? gstinNumber;
  final String? lossDate; // ISO
  final String? confirmedItemsJson;

  DraftClaim({
    required this.id,
    required this.createdAt,
    this.userCategory,
    this.photoPath,
    this.geotag = '',
    this.timestamp = '',
    this.photoLat,
    this.photoLon,
    this.photoCapturedAt,
    this.policyPdfPath,
    this.policyPdfName,
    this.govtIdPath,
    this.govtIdName,
    this.invoicePath,
    this.invoiceName,
    this.itemName = '',
    this.itemType,
    this.permanentAddress = '',
    this.businessType,
    this.gstinNumber,
    this.lossDate,
    this.confirmedItemsJson,
  });

  Map<String, dynamic> toJson() => {
        'id': id,
        'createdAt': createdAt,
        'userCategory': userCategory,
        'photoPath': photoPath,
        'geotag': geotag,
        'timestamp': timestamp,
        'photoLat': photoLat,
        'photoLon': photoLon,
        'photoCapturedAt': photoCapturedAt,
        'policyPdfPath': policyPdfPath,
        'policyPdfName': policyPdfName,
        'govtIdPath': govtIdPath,
        'govtIdName': govtIdName,
        'invoicePath': invoicePath,
        'invoiceName': invoiceName,
        'itemName': itemName,
        'itemType': itemType,
        'permanentAddress': permanentAddress,
        'businessType': businessType,
        'gstinNumber': gstinNumber,
        'lossDate': lossDate,
        'confirmedItemsJson': confirmedItemsJson,
      };

  factory DraftClaim.fromJson(Map<String, dynamic> m) => DraftClaim(
        id: (m['id'] ?? '').toString(),
        createdAt: (m['createdAt'] ?? '').toString(),
        userCategory: m['userCategory'] as String?,
        photoPath: m['photoPath'] as String?,
        geotag: (m['geotag'] ?? '').toString(),
        timestamp: (m['timestamp'] ?? '').toString(),
        photoLat: (m['photoLat'] as num?)?.toDouble(),
        photoLon: (m['photoLon'] as num?)?.toDouble(),
        photoCapturedAt: m['photoCapturedAt'] as String?,
        policyPdfPath: m['policyPdfPath'] as String?,
        policyPdfName: m['policyPdfName'] as String?,
        govtIdPath: m['govtIdPath'] as String?,
        govtIdName: m['govtIdName'] as String?,
        invoicePath: m['invoicePath'] as String?,
        invoiceName: m['invoiceName'] as String?,
        itemName: (m['itemName'] ?? '').toString(),
        itemType: m['itemType'] as String?,
        permanentAddress: (m['permanentAddress'] ?? '').toString(),
        businessType: m['businessType'] as String?,
        gstinNumber: m['gstinNumber'] as String?,
        lossDate: m['lossDate'] as String?,
        confirmedItemsJson: m['confirmedItemsJson'] as String?,
      );
}

class DraftStore {
  static Future<Directory> _dir() async {
    final base = await getApplicationDocumentsDirectory();
    final d = Directory('${base.path}/claim_drafts');
    if (!await d.exists()) await d.create(recursive: true);
    return d;
  }

  static String _newId() =>
      'DRAFT-${DateTime.now().millisecondsSinceEpoch.toRadixString(16).toUpperCase()}';

  /// Copy a picked file into the drafts dir so it outlives the OS cache. Returns
  /// the persistent path (or the original on failure / if already persisted).
  static Future<String?> _persist(String? src, Directory dir, String id, String tag) async {
    if (src == null || src.isEmpty) return null;
    try {
      if (src.startsWith(dir.path)) return src; // already ours
      final f = File(src);
      if (!await f.exists()) return src;
      final ext = src.contains('.') ? src.substring(src.lastIndexOf('.')) : '';
      final dest = '${dir.path}/${id}_$tag$ext';
      await f.copy(dest);
      return dest;
    } catch (_) {
      return src;
    }
  }

  static Future<List<DraftClaim>> load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_kDraftsKey);
    if (raw == null || raw.isEmpty) return [];
    try {
      final list = (jsonDecode(raw) as List);
      return list
          .map((m) => DraftClaim.fromJson(Map<String, dynamic>.from(m as Map)))
          .toList();
    } catch (_) {
      return [];
    }
  }

  static Future<void> _saveAll(List<DraftClaim> drafts) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_kDraftsKey, jsonEncode(drafts.map((d) => d.toJson()).toList()));
  }

  /// Create or update (when [existingId] is given) a draft, persisting its files.
  static Future<DraftClaim> save({
    String? existingId,
    String? userCategory,
    String? photoPath,
    String geotag = '',
    String timestamp = '',
    double? photoLat,
    double? photoLon,
    String? photoCapturedAt,
    String? policyPdfPath,
    String? policyPdfName,
    String? govtIdPath,
    String? govtIdName,
    String? invoicePath,
    String? invoiceName,
    String itemName = '',
    String? itemType,
    String permanentAddress = '',
    String? businessType,
    String? gstinNumber,
    DateTime? lossDate,
    String? confirmedItemsJson,
  }) async {
    final dir = await _dir();
    final id = existingId ?? _newId();
    final draft = DraftClaim(
      id: id,
      createdAt: DateTime.now().toIso8601String(),
      userCategory: userCategory,
      photoPath: await _persist(photoPath, dir, id, 'photo'),
      geotag: geotag,
      timestamp: timestamp,
      photoLat: photoLat,
      photoLon: photoLon,
      photoCapturedAt: photoCapturedAt,
      policyPdfPath: await _persist(policyPdfPath, dir, id, 'policy'),
      policyPdfName: policyPdfName,
      govtIdPath: await _persist(govtIdPath, dir, id, 'govtid'),
      govtIdName: govtIdName,
      invoicePath: await _persist(invoicePath, dir, id, 'invoice'),
      invoiceName: invoiceName,
      itemName: itemName,
      itemType: itemType,
      permanentAddress: permanentAddress,
      businessType: businessType,
      gstinNumber: gstinNumber,
      lossDate: lossDate?.toIso8601String(),
      confirmedItemsJson: confirmedItemsJson,
    );
    final drafts = await load();
    drafts.removeWhere((d) => d.id == id);
    drafts.insert(0, draft);
    await _saveAll(drafts);
    return draft;
  }

  static Future<void> delete(String id) async {
    final drafts = await load();
    final gone = drafts.where((d) => d.id == id).toList();
    drafts.removeWhere((d) => d.id == id);
    await _saveAll(drafts);
    for (final d in gone) {
      for (final p in [d.photoPath, d.policyPdfPath, d.govtIdPath, d.invoicePath]) {
        if (p != null && p.isNotEmpty) {
          try {
            final f = File(p);
            if (await f.exists()) await f.delete();
          } catch (_) {}
        }
      }
    }
  }
}

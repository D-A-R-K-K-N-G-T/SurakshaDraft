import 'dart:math';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Identity + request-header helpers for talking to the gateway (Phase 7/8).
///
/// The gateway forwards `Authorization` to the pipeline, which resolves it to a
/// user (see the backend `auth_mode`). We send a STABLE subject so the same user
/// keeps the same server-side identity across app restarts:
///
///   * signed in with Google  -> the Firebase `uid` (stable, and the same after a
///     reinstall as long as they sign in with the same account);
///   * otherwise               -> a per-install device id kept in SharedPreferences.
///
/// This pairs with the backend running `auth_mode="demo"` (the token IS the
/// subject). For real token verification set the backend to `auth_mode="firebase"`
/// and change [subjectToken] to return `await user.getIdToken()` instead of the uid.
String _randomHex(int nBytes) {
  final r = Random.secure();
  return List<int>.generate(nBytes, (_) => r.nextInt(256))
      .map((b) => b.toRadixString(16).padLeft(2, '0'))
      .join();
}

Future<String> subjectToken() async {
  final user = FirebaseAuth.instance.currentUser;
  if (user != null && user.uid.isNotEmpty) {
    return user.uid; // stable subject; survives reinstall with the same account
  }
  final prefs = await SharedPreferences.getInstance();
  var id = prefs.getString('device_id');
  if (id == null || id.isEmpty) {
    id = 'dev-${_randomHex(16)}';
    await prefs.setString('device_id', id);
  }
  return id;
}

/// Authorization header for gateway calls ({} when we somehow have no subject).
Future<Map<String, String>> authHeaders() async {
  final token = await subjectToken();
  return token.isEmpty ? {} : {'Authorization': 'Bearer $token'};
}

/// A fresh idempotency key. Generate ONE per submit attempt and reuse it across
/// retries of that attempt, so a timed-out-but-processed submit is not duplicated.
String newIdempotencyKey() => _randomHex(16);

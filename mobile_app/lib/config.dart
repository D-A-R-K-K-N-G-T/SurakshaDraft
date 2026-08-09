/// Central API configuration.
///
/// `10.0.2.2` is the Android emulator's alias for the host machine's
/// `localhost`. Override for physical devices / iOS / web with:
///   flutter run --dart-define=API_BASE=http://192.168.1.5:3000
const String kApiBase = String.fromEnvironment(
  'API_BASE',
  defaultValue: 'http://10.0.2.2:3000',
);

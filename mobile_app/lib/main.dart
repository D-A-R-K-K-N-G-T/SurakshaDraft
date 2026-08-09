import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:local_auth/local_auth.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'screens/item_list_screen.dart';
import 'screens/policy_onboarding_screen.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  try {
    await Firebase.initializeApp();
  } catch (e) {
    debugPrint('Firebase init notice: $e');
  }
  runApp(const MyApp());
}

// Global Language Notifier
final ValueNotifier<String> appLanguage = ValueNotifier<String>('English');

// Comprehensive Indian Regional Languages Dictionary
final Map<String, Map<String, String>> localizedStrings = {
  'English': {
    'welcome': 'Welcome to SurakshaDraft',
    'select_lang': 'Select your preferred language',
    'next': 'Next',
    'choose_type': 'Select User Category',
    'personal': 'Personal',
    'commercial': 'Commercial',
    'insurance': 'Insurance Firm',
    'signin': 'Sign in / Sign up to continue',
    'google_btn': 'Sign in with Google',
    'home_title': 'Dashboard',
    'logout': 'Sign Out',
  },
  'Hindi': {
    'welcome': 'सुरक्षाड्राफ्ट में आपका स्वागत है',
    'select_lang': 'अपनी पसंदीदा भाषा चुनें',
    'next': 'आगे बढ़ें',
    'choose_type': 'उपयोगकर्ता श्रेणी चुनें',
    'personal': 'व्यक्तिगत (Personal)',
    'commercial': 'व्यावसायिक (Commercial)',
    'insurance': 'बीमा कंपनी (Insurance Firm)',
    'signin': 'आगे बढ़ने के लिए साइन इन / साइन अप करें',
    'google_btn': 'गूगल से साइन इन करें',
    'home_title': 'डैशबोर्ड',
    'logout': 'साइन आउट',
  },
  'Tamil': {
    'welcome': 'சுரக்ஷாட்ராஃப்ட்-க்கு வரவேற்கிறோம்',
    'select_lang': 'உங்கள் மொழியைத் தேர்ந்தெடுக்கவும்',
    'next': 'அடுத்து',
    'choose_type': 'பயனர் வகையைத் தேர்ந்தெடுக்கவும்',
    'personal': 'தனிப்பட்ட (Personal)',
    'commercial': 'வணிக (Commercial)',
    'insurance': 'காப்பீட்டு நிறுவனம் (Insurance Firm)',
    'signin': 'தொடர உள்நுழையவும் / பதிவுசெய்யவும்',
    'google_btn': 'Google மூலம் உள்நுழையவும்',
    'home_title': 'டாஷ்போர்டு',
    'logout': 'வெளியேறு',
  },
  'Telugu': {
    'welcome': 'సురక్షాడ్రాఫ్ట్‌కి స్వాగతం',
    'select_lang': 'మీ భాషను ఎంచుకోండి',
    'next': 'తరువాత',
    'choose_type': 'వినియోగదారు రకాన్ని ఎంచుకోండి',
    'personal': 'వ్యక్తిగత (Personal)',
    'commercial': 'వాణిజ్య (Commercial)',
    'insurance': 'ఇన్సూరెన్స్ సంస్థ (Insurance Firm)',
    'signin': 'కొనసాగడానికి సైన్ ఇన్ / సైన్ అప్ చేయండి',
    'google_btn': 'Googleతో సైన్ ఇన్ చేయండి',
    'home_title': 'డాష్‌బోర్డ్',
    'logout': 'సైన్ అవుట్',
  },
  'Kannada': {
    'welcome': 'ಸುರಕ್ಷಾಡ್ರಾಫ್ಟ್‌ಗೆ స్వాగതം',
    'select_lang': 'ನಿಮ್ಮ ಭಾಷೆಯನ್ನು ಆಯ್ಕೆಮಾಡಿ',
    'next': 'ಮುಂದೆ',
    'choose_type': 'ಬಳಕೆದಾರರ ವರ್ಗವನ್ನು ಆಯ್ಕೆಮಾಡಿ',
    'personal': 'ವೈಯಕ್ತಿಕ (Personal)',
    'commercial': 'ವಾಣಿಜ್ಯ (Commercial)',
    'insurance': 'ವಿಮಾ ಸಂಸ್ಥೆ (Insurance Firm)',
    'signin': 'ಮುಂದುವರಿಯಲು ಸೈನ್ ইন / ಸೈನ್ അപ്പ് ಮಾಡಿ',
    'google_btn': 'Google นೊಂದಿಗೆ ಸೈನ್ ಇನ್ ಮಾಡಿ',
    'home_title': 'ಡ್ಯಾಶ್‌ಬೋರ್ಡ್',
    'logout': 'ಸೈನ್ ಔಟ್',
  },
  'Marathi': {
    'welcome': 'सुरक्षाड्राफ्टमध्ये आपले स्वागत आहे',
    'select_lang': 'आपली आवडती भाषा निवडा',
    'next': 'पुढील',
    'choose_type': 'वापरकर्ता श्रेणी निवडा',
    'personal': 'वैयक्तिक (Personal)',
    'commercial': 'व्यावसायिक (Commercial)',
    'insurance': 'विमा कंपनी (Insurance Firm)',
    'signin': 'पुढे जाण्यासाठी साइन इन / साइन अप करा',
    'google_btn': 'गूगलद्वारे साइन इन करा',
    'home_title': 'डॅशबोर्ड',
    'logout': 'साइन आउट',
  },
  'Bengali': {
    'welcome': 'সুরক্ষাড্রাফটে আপনাকে স্বাগতম',
    'select_lang': 'আপনার পছন্দের ভাষা নির্বাচন করুন',
    'next': 'পরবর্তী',
    'choose_type': 'ব্যবহারকারীর বিভাগ নির্বাচন করুন',
    'personal': 'ব্যক্তিগত (Personal)',
    'commercial': 'বাণিজ্যিক (Commercial)',
    'insurance': 'বীমা সংস্থা (Insurance Firm)',
    'signin': 'চালিয়ে যেতে সাইন ইন / সাইন আপ করুন',
    'google_btn': 'Google দিয়ে সাইন ইন করুন',
    'home_title': 'ড্যাশবোর্ড',
    'logout': 'সাইন আউট',
  },
  'Gujarati': {
    'welcome': 'સુરેક્ષાડ્રાફ્ટમાં આપનું સ્વાગત છે',
    'select_lang': 'તમારી પસંદગીની ભાષા પસંદ કરો',
    'next': 'આગળ',
    'choose_type': 'વપરાશકર્તા શ્રેણી પસંદ કરો',
    'personal': 'વ્યક્તિગત (Personal)',
    'commercial': 'વ્યાવસાયિક (Commercial)',
    'insurance': 'વીમા કંપની (Insurance Firm)',
    'signin': 'આગળ વધવા માટે સાઇન ઇન / સાઇન અપ કરો',
    'google_btn': 'Google સાથે સાઇન ઇન કરો',
    'home_title': 'ડેશબોર્ડ',
    'logout': 'સાઇન આઉટ',
  },
  'Malayalam': {
    'welcome': 'സുരക്ഷാഡ്രാഫ്ടിലേക്ക് സ്വാഗതം',
    'select_lang': 'നിങ്ങളുടെ ഭാഷ തിരഞ്ഞെടുക്കുക',
    'next': 'അടുത്തത്',
    'choose_type': 'ഉപയോക്തൃ വിഭാഗം തിരഞ്ഞെടുക്കുക',
    'personal': 'വ്യക്തിഗതം (Personal)',
    'commercial': 'വാണിജ്യം (Commercial)',
    'insurance': 'ഇൻഷുറൻസ് സ്ഥാപനം (Insurance Firm)',
    'signin': 'തുടരാൻ സൈൻ ഇൻ / സൈൻ അപ്പ് ചെയ്യുക',
    'google_btn': 'Google ഉപയോഗിച്ച് സൈൻ ഇൻ ചെയ്യുക',
    'home_title': 'ഡാഷ്‌ബോർഡ്',
    'logout': 'സൈൻ ഔട്ട്',
  },
  'Punjabi': {
    'welcome': 'ਸੁਰੱਖਿਆਡਰਾਫਟ ਵਿੱਚ ਤੁਹਾਡਾ ਸੁਆਗਤ ਹੈ',
    'select_lang': 'ਆਪਣੀ ਪਸੰਦੀਦਾ ਭਾਸ਼ਾ ਚੁਣੋ',
    'next': 'ਅੱਗੇ',
    'choose_type': 'ਵਰਤੋਂਕਾਰ ਸ਼੍ਰੇਣੀ ਚੁਣੋ',
    'personal': 'ਨਿੱਜੀ (Personal)',
    'commercial': 'ਵਪਾਰਕ (Commercial)',
    'insurance': 'ਬੀਮਾ ਫਰਮ (Insurance Firm)',
    'signin': 'ਜਾਰੀ ਰੱਖਣ ਲਈ ਸਾਈਨ ਇਨ / ਸਾਈਨ ਅੱਪ ਕਰੋ',
    'google_btn': 'Google ਨਾਲ ਸਾਈਨ ਇਨ ਕਰੋ',
    'home_title': 'ਡੈਸ਼ਬੋਰਡ',
    'logout': 'ਸਾਈਨ ਆਊਟ',
  },
};

String getText(String key) {
  return localizedStrings[appLanguage.value]?[key] ??
      localizedStrings['English']![key]!;
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<String>(
      valueListenable: appLanguage,
      builder: (context, lang, child) {
        return MaterialApp(
          title: 'SurakshaDraft',
          debugShowCheckedModeBanner: false,
          theme: ThemeData(
            colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF4F46E5)),
            useMaterial3: true,
          ),
          routes: {
            '/': (context) => const BiometricUnlockScreen(),
            '/auth': (context) => const AuthScreen(),
            '/policy': (context) => const PolicyOnboardingScreen(),
            '/hub': (context) => const ItemListScreen(),
          },
        );
      },
    );
  }
}

Future<void> navigateNextAfterAuth(BuildContext context) async {
  final prefs = await SharedPreferences.getInstance();
  final bool policyComplete = prefs.getBool('policy_setup_complete') ?? false;

  if (!context.mounted) return;

  if (policyComplete) {
    Navigator.of(context).pushNamedAndRemoveUntil('/hub', (route) => false);
  } else {
    Navigator.of(context).pushNamedAndRemoveUntil('/policy', (route) => false);
  }
}

// -----------------------------------------------------------------------------
// PAGE 1: BIOMETRIC UNLOCK (FIRST STEP)
// -----------------------------------------------------------------------------
class BiometricUnlockScreen extends StatefulWidget {
  const BiometricUnlockScreen({super.key});

  @override
  State<BiometricUnlockScreen> createState() => _BiometricUnlockScreenState();
}

class _BiometricUnlockScreenState extends State<BiometricUnlockScreen> {
  final LocalAuthentication _auth = LocalAuthentication();
  bool _scanning = false;

  @override
  void initState() {
    super.initState();
    _authenticate();
  }

  Future<void> _authenticate() async {
    try {
      final bool canAuth = await _auth.canCheckBiometrics ||
          await _auth.isDeviceSupported();

      if (!canAuth) {
        _proceedNext();
        return;
      }

      setState(() => _scanning = true);

      final bool authenticated = await _auth.authenticate(
        localizedReason: 'Give biometric authentication to access SurakshaDraft',
        options: const AuthenticationOptions(
          biometricOnly: false,
          stickyAuth: true,
        ),
      );

      setState(() => _scanning = false);

      if (authenticated) {
        _proceedNext();
      }
    } on PlatformException catch (_) {
      setState(() => _scanning = false);
      _proceedNext();
    }
  }

  Future<void> _proceedNext() async {
    final prefs = await SharedPreferences.getInstance();
    final bool isSignedIn = prefs.getBool('is_signed_in') ?? false;
    final String? savedLang = prefs.getString('app_language');

    if (savedLang != null && localizedStrings.containsKey(savedLang)) {
      appLanguage.value = savedLang;
    }

    if (!mounted) return;

    if (isSignedIn) {
      // User is already signed in -> keep signed in & open directly
      navigateNextAfterAuth(context);
    } else {
      // First time / not signed in -> Proceed to Page 2: Language Selection
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const LanguageSelectionScreen()),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF8FAFC),
      body: SafeArea(
        child: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.security, size: 80, color: Color(0xFF4F46E5)),
              const SizedBox(height: 24),
              const Text(
                'SURAKSHADRAFT',
                style: TextStyle(
                  fontSize: 24,
                  fontWeight: FontWeight.bold,
                  letterSpacing: 1.0,
                  color: Color(0xFF0F172A),
                ),
              ),
              const SizedBox(height: 12),
              const Text(
                'Biometric Authentication Required',
                style: TextStyle(color: Colors.grey, fontSize: 13),
              ),
              const SizedBox(height: 48),
              GestureDetector(
                onTap: _authenticate,
                child: Container(
                  padding: const EdgeInsets.all(24),
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: const Color(0xFF4F46E5).withOpacity(0.1),
                    border: Border.all(color: const Color(0xFF4F46E5), width: 2),
                  ),
                  child: Icon(
                    _scanning ? Icons.hourglass_top : Icons.fingerprint,
                    size: 64,
                    color: const Color(0xFF4F46E5),
                  ),
                ),
              ),
              const SizedBox(height: 16),
              Text(
                _scanning ? 'Scanning Biometrics...' : 'Touch sensor to unlock',
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// -----------------------------------------------------------------------------
// PAGE 2: APP LOGO & INDIAN REGIONAL LANGUAGES DROPDOWN
// -----------------------------------------------------------------------------
class LanguageSelectionScreen extends StatelessWidget {
  const LanguageSelectionScreen({super.key});

  final List<String> languages = const [
    'English',
    'Hindi',
    'Tamil',
    'Telugu',
    'Kannada',
    'Marathi',
    'Bengali',
    'Gujarati',
    'Malayalam',
    'Punjabi',
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF8FAFC),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(28.0),
          child: Column(
            children: [
              const Spacer(),
              // App Logo
              Image.asset(
                'assets/placeholder.png',
                height: 140,
                fit: BoxFit.contain,
              ),
              const SizedBox(height: 28),
              ValueListenableBuilder<String>(
                valueListenable: appLanguage,
                builder: (context, lang, child) {
                  return Text(
                    getText('welcome'),
                    style: const TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.bold,
                      color: Color(0xFF0F172A),
                    ),
                    textAlign: TextAlign.center,
                  );
                },
              ),
              const SizedBox(height: 8),
              ValueListenableBuilder<String>(
                valueListenable: appLanguage,
                builder: (context, lang, child) {
                  return Text(
                    getText('select_lang'),
                    style: const TextStyle(fontSize: 14, color: Colors.grey),
                    textAlign: TextAlign.center,
                  );
                },
              ),
              const SizedBox(height: 24),
              // Indian Regional Languages Dropdown
              ValueListenableBuilder<String>(
                valueListenable: appLanguage,
                builder: (context, currentLang, child) {
                  return Container(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                    decoration: BoxDecoration(
                      color: Colors.white,
                      border: Border.all(color: const Color(0xFF4F46E5), width: 1.5),
                      borderRadius: BorderRadius.circular(14),
                      boxShadow: [
                        BoxShadow(
                          color: Colors.black.withOpacity(0.04),
                          blurRadius: 8,
                          offset: const Offset(0, 2),
                        ),
                      ],
                    ),
                    child: DropdownButtonHideUnderline(
                      child: DropdownButton<String>(
                        value: languages.contains(currentLang) ? currentLang : 'English',
                        isExpanded: true,
                        icon: const Icon(Icons.language, color: Color(0xFF4F46E5)),
                        items: languages.map((String lang) {
                          return DropdownMenuItem<String>(
                            value: lang,
                            child: Text(
                              lang,
                              style: const TextStyle(
                                fontSize: 16,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          );
                        }).toList(),
                        onChanged: (String? newLang) async {
                          if (newLang != null) {
                            appLanguage.value = newLang;
                            final prefs = await SharedPreferences.getInstance();
                            await prefs.setString('app_language', newLang);
                          }
                        },
                      ),
                    ),
                  );
                },
              ),
              const Spacer(),
              ValueListenableBuilder<String>(
                valueListenable: appLanguage,
                builder: (context, lang, child) {
                  return SizedBox(
                    width: double.infinity,
                    height: 52,
                    child: ElevatedButton(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFF4F46E5),
                        foregroundColor: Colors.white,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(12),
                        ),
                      ),
                      onPressed: () {
                        Navigator.of(context).push(
                          MaterialPageRoute(
                              builder: (_) => const CategorySelectionScreen()),
                        );
                      },
                      child: Text(
                        getText('next'),
                        style: const TextStyle(
                            fontSize: 18, fontWeight: FontWeight.bold),
                      ),
                    ),
                  );
                },
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// -----------------------------------------------------------------------------
// PAGE 3: CATEGORY SELECTION (Personal / Commercial / Insurance Firm)
// -----------------------------------------------------------------------------
class CategorySelectionScreen extends StatefulWidget {
  const CategorySelectionScreen({super.key});

  @override
  State<CategorySelectionScreen> createState() => _CategorySelectionScreenState();
}

class _CategorySelectionScreenState extends State<CategorySelectionScreen> {
  String selectedCategory = 'Personal';

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF8FAFC),
      appBar: AppBar(
        title: Text(getText('choose_type')),
        backgroundColor: Colors.white,
        elevation: 0.5,
        foregroundColor: const Color(0xFF0F172A),
      ),
      body: SafeArea(
        child: Padding( 
          padding: const EdgeInsets.all(24.0),
          child: Column(
            children: [
              _buildCategoryTile('Personal', Icons.person, getText('personal')),
              const SizedBox(height: 16),
              _buildCategoryTile('Commercial', Icons.business, getText('commercial')),
              const SizedBox(height: 16),
              _buildCategoryTile('Insurance Firm', Icons.verified_user, getText('insurance')),
              const Spacer(),
              SizedBox(
                width: double.infinity,
                height: 52,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF4F46E5),
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(12),
                    ),
                  ),
                  onPressed: () async {
                    final prefs = await SharedPreferences.getInstance();
                    await prefs.setString('user_category', selectedCategory);

                    if (!mounted) return;
                    Navigator.of(context).push(
                      MaterialPageRoute(builder: (_) => const AuthScreen()),
                    );
                  },
                  child: Text(
                    getText('next'),
                    style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildCategoryTile(String key, IconData icon, String title) {
    final bool isSelected = selectedCategory == key;
    return InkWell(
      onTap: () => setState(() => selectedCategory = key),
      child: Container(
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
          color: isSelected ? const Color(0xFF4F46E5).withOpacity(0.08) : Colors.white,
          border: Border.all(
            color: isSelected ? const Color(0xFF4F46E5) : Colors.grey.shade300,
            width: isSelected ? 2 : 1,
          ),
          borderRadius: BorderRadius.circular(14),
        ),
        child: Row(
          children: [
            Icon(icon, size: 32, color: isSelected ? const Color(0xFF4F46E5) : Colors.grey),
            const SizedBox(width: 16),
            Expanded(
              child: Text(
                title,
                style: TextStyle(
                  fontSize: 17,
                  fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
                  color: isSelected ? const Color(0xFF4F46E5) : const Color(0xFF0F172A),
                ),
              ),
            ),
            if (isSelected)
              const Icon(Icons.check_circle, color: Color(0xFF4F46E5), size: 22),
          ],
        ),
      ),
    );
  }
}

// -----------------------------------------------------------------------------
// PAGE 4: GOOGLE SIGN UP / SIGN IN & PERSISTENCE
// -----------------------------------------------------------------------------
class AuthScreen extends StatefulWidget {
  const AuthScreen({super.key});

  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> {
  bool _isLoading = false;
  String? _userCategory;
  final GoogleSignIn _googleSignIn = GoogleSignIn(
    scopes: ['email', 'profile'],
    serverClientId: '804457732869-j0csnv8k9f5tualkv4p6nicb59gaj1pk.apps.googleusercontent.com',
  );

  final _firmNameController = TextEditingController();
  final _firmPasswordController = TextEditingController();

  @override
  void initState() {
    super.initState();
    _loadCategory();
  }

  Future<void> _loadCategory() async {
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      _userCategory = prefs.getString('user_category') ?? 'Personal';
    });
  }

  Future<void> _handleFirmSignIn() async {
    if (_firmNameController.text.trim().toUpperCase() == 'ABC' && _firmPasswordController.text == '1234') {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool('is_signed_in', true);
      await prefs.setString('policy_business_name', 'ABC Insurance Dashboard');
      await prefs.setString('user_email', 'admin@abc.com');
      await prefs.setString('user_name', 'ABC Firm Admin');
      if (!mounted) return;
      navigateNextAfterAuth(context);
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Invalid Firm Credentials. Hint: use ABC and 1234'),
          backgroundColor: Colors.redAccent,
        ),
      );
    }
  }

  Future<void> _handleGoogleSignIn() async {
    setState(() => _isLoading = true);

    try {
      final GoogleSignInAccount? googleUser = await _googleSignIn.signIn();

      if (googleUser != null) {
        try {
          final GoogleSignInAuthentication googleAuth = await googleUser.authentication;
          final AuthCredential credential = GoogleAuthProvider.credential(
            accessToken: googleAuth.accessToken,
            idToken: googleAuth.idToken,
          );
          await FirebaseAuth.instance.signInWithCredential(credential);
        } catch (e) {
          debugPrint('FirebaseAuth notice: $e');
        }

        final prefs = await SharedPreferences.getInstance();
        await prefs.setBool('is_signed_in', true); // PERSIST LOGGED IN STATE
        await prefs.setString('user_email', googleUser.email);
        await prefs.setString('user_name', googleUser.displayName ?? '');
        await prefs.setString('user_photo', googleUser.photoUrl ?? '');

        if (!mounted) return;
        navigateNextAfterAuth(context);
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Google Sign-In Notice: $e\nTap "Dev Bypass" to test offline without GCP keys.'),
          duration: const Duration(seconds: 5),
          action: SnackBarAction(
            label: 'Dev Bypass',
            textColor: Colors.amber,
            onPressed: () async {
              final prefs = await SharedPreferences.getInstance();
              await prefs.setBool('is_signed_in', true); // PERSIST LOGGED IN STATE
              await prefs.setString('user_email', 'user@example.com');
              if (!mounted) return;
              navigateNextAfterAuth(context);
            },
          ),
        ),
      );
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_userCategory == null) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }

    if (_userCategory == 'Insurance Firm') {
      return _buildFirmLogin();
    }

    return Scaffold(
      backgroundColor: const Color(0xFFF8FAFC),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(28.0),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Container(
                padding: const EdgeInsets.all(20),
                decoration: BoxDecoration(
                  color: const Color(0xFFEEF2FF),
                  shape: BoxShape.circle,
                ),
                child: const Icon(Icons.shield_outlined, size: 64, color: Color(0xFF4F46E5)),
              ),
              const SizedBox(height: 24),
              Text(
                getText('signin'),
                style: const TextStyle(
                  fontSize: 22,
                  fontWeight: FontWeight.bold,
                  color: Color(0xFF0F172A),
                ),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 12),
              const Text(
                'Sign in or Sign up with your Google Account. Once signed in, you will stay logged in when re-opening the app.',
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.grey, fontSize: 13),
              ),
              const SizedBox(height: 44),
              _isLoading
                  ? const CircularProgressIndicator()
                  : OutlinedButton.icon(
                      style: OutlinedButton.styleFrom(
                        minimumSize: const Size.fromHeight(52),
                        backgroundColor: Colors.white,
                        side: const BorderSide(color: Colors.grey),
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(12),
                        ),
                      ),
                      onPressed: _handleGoogleSignIn,
                      icon: const Icon(Icons.g_mobiledata, size: 36, color: Colors.blue),
                      label: Text(
                        getText('google_btn'),
                        style: const TextStyle(
                          fontSize: 17,
                          fontWeight: FontWeight.bold,
                          color: Color(0xFF0F172A),
                        ),
                      ),
                    ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildFirmLogin() {
    return Scaffold(
      backgroundColor: const Color(0xFFF8FAFC),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(28.0),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Container(
                padding: const EdgeInsets.all(20),
                decoration: BoxDecoration(
                  color: const Color(0xFFEEF2FF),
                  shape: BoxShape.circle,
                ),
                child: const Icon(Icons.business, size: 64, color: Color(0xFF4F46E5)),
              ),
              const SizedBox(height: 24),
              const Text(
                'Insurance Firm Login',
                style: TextStyle(
                  fontSize: 22,
                  fontWeight: FontWeight.bold,
                  color: Color(0xFF0F172A),
                ),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 32),
              TextField(
                controller: _firmNameController,
                decoration: InputDecoration(
                  labelText: 'Firm Name',
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
                  prefixIcon: const Icon(Icons.business_center),
                ),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: _firmPasswordController,
                obscureText: true,
                decoration: InputDecoration(
                  labelText: 'Password',
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
                  prefixIcon: const Icon(Icons.lock),
                ),
              ),
              const SizedBox(height: 32),
              SizedBox(
                width: double.infinity,
                height: 52,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF4F46E5),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(12),
                    ),
                  ),
                  onPressed: _handleFirmSignIn,
                  child: const Text(
                    'Login',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 18,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// -----------------------------------------------------------------------------
// PAGE 5: DASHBOARD / HOME PAGE (SurakshaDraft Hub)
// -----------------------------------------------------------------------------
class HomePage extends StatelessWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context) {
    return const ItemListScreen();
  }
}
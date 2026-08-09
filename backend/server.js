const express = require('express');
const multer = require('multer');
const cors = require('cors');
const axios = require('axios');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');

const app = express();
const port = process.env.PORT || 3000;
const PYTHON_BACKEND_URL = 'http://127.0.0.1:8000';

app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Log every incoming request so we can SEE whether the app reaches the gateway.
app.use((req, res, next) => {
  console.log(`[${new Date().toISOString()}] ${req.method} ${req.originalUrl} from ${req.ip}`);
  res.on('finish', () => console.log(`  -> ${req.method} ${req.originalUrl} ${res.statusCode}`));
  next();
});

// Set up multer for file uploads
const storage = multer.diskStorage({
  destination: function (req, file, cb) {
    const dir = 'uploads/';
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    cb(null, dir);
  },
  filename: function (req, file, cb) {
    cb(null, Date.now() + '-' + file.originalname);
  }
});
const upload = multer({ storage: storage });

// The claim form uploads under named fields so the gateway can route each file
// by ROLE, not by sniffing MIME type (a JPEG Aadhaar card must never be treated
// as damage evidence). We use upload.any() rather than upload.fields([...]) so
// an unexpected field name (e.g. an older app build sending everything under
// "files") does not throw "Unexpected field" and crash the request — we route
// what we recognize and log the rest.
const CLAIM_UPLOAD_FIELDS = upload.any();

// Group multer's flat req.files array (from upload.any()) by fieldname into the
// role buckets constructClaimState expects.
function groupFilesByField(fileArray) {
  const grouped = { photos: [], invoices: [], govt_id: [], policy_doc: [] };
  (fileArray || []).forEach((f) => {
    if (f.fieldname === 'photos' || f.fieldname === 'files') {
      // "files" is the legacy single-bucket field from older app builds; treat
      // those as damage photos (best effort) so old clients still function.
      grouped.photos.push(f);
    } else if (grouped[f.fieldname]) {
      grouped[f.fieldname].push(f);
    } else {
      console.warn(`Ignoring file on unrecognized field "${f.fieldname}"`);
    }
  });
  return grouped;
}

// UI category label (from the app's _itemTypesByCategory) -> pipeline category.
// The pipeline's sum-insured ceiling keys off these, and vision uses them as the
// declared category set, so a clean mapping matters. Splitting the raw label on
// "," (the old behaviour) produced bogus two-element categories.
const CATEGORY_MAP = {
  // Personal
  'Vehicle (Car / Bike)': 'Vehicle',
  'Home / Property Asset': 'Property',
  'Personal Electronics & Appliances': 'Electronics',
  'Jewelry & Valuables': 'Valuables',
  'Furniture & Fixtures': 'Furniture, Fixtures & Fittings',
  'Other Personal Property': 'Other',
  // Commercial
  'Industrial Machinery & Equipment': 'Plant & Machinery',
  'Stock, Inventory & Raw Materials': 'Stock',
  'Commercial Property & Building': 'Property',
  'Commercial Fleet / Cargo Vehicle': 'Vehicle',
  'IT Hardware & Office Electronics': 'Electronics',
  'Tools & Trade Supplies': 'Plant & Machinery',
  // Insurance Firm
  'Commercial Asset Portfolio': 'Stock',
  'Reinsurance Policy Claim': 'Other',
  'Motor Third-Party / Fleet Claim': 'Vehicle',
  'Engineering & Construction Risk': 'Plant & Machinery',
  'Marine & Cargo Transit': 'Stock',
  'General Casualty Claim': 'Other',
};

function mapCategories(raw) {
  if (!raw) return ['Stock'];
  // `raw` is a single UI label. Map it; keep the raw label as a fallback so an
  // unmapped label still reaches vision as *something* meaningful.
  const mapped = CATEGORY_MAP[raw.trim()];
  return [mapped || raw.trim()];
}

function sha256OfFile(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function numOrNull(v) {
  if (v === undefined || v === null || v === '') return null;
  const n = parseFloat(v);
  return isNaN(n) ? null : n;
}

// Builds the policy block from what the CLIENT knows (onboarding fields).
// Anything the client doesn't supply is left absent rather than invented — the
// pipeline's policy_extract node then fills it from the uploaded policy
// schedule, which is the authoritative source. Only if BOTH are missing do we
// fall back to generic terms, and that fallback is reported in `assumedFields`
// so the claim pack never presents fabricated policy data as real.
function buildPolicy(body) {
  const assumedFields = [];
  const policy = {
    product: body.product || 'Standard Flood Protection',
    asset_categories: mapCategories(body.categories),
  };

  if (body.policy_number) policy.policy_number = body.policy_number;
  if (body.policy_insurer) policy.insurer = body.policy_insurer;

  const sumInsured = numOrNull(body.policy_sum_insured);
  const excess = numOrNull(body.policy_excess);
  if (sumInsured !== null) policy.sum_insured_stock = sumInsured;
  if (excess !== null) policy.excess = excess;

  if (body.policy_start_date) policy.start_date = body.policy_start_date;
  if (body.policy_end_date) policy.end_date = body.policy_end_date;
  if (body.clauses) { policy.clauses = body.clauses; policy.clauses_assumed = false; }

  // Fallbacks only where the client supplied nothing. policy_extract may still
  // override these from the uploaded schedule.
  if (!policy.start_date) { policy.start_date = '2025-01-01T00:00:00Z'; assumedFields.push('start_date'); }
  if (!policy.end_date) { policy.end_date = '2026-12-31T23:59:59Z'; assumedFields.push('end_date'); }
  if (policy.sum_insured_stock === undefined) { policy.sum_insured_stock = 1000000; assumedFields.push('sum_insured_stock'); }
  if (!policy.clauses) {
    policy.clauses = 'Flood damage to stock and machinery is covered.';
    // Machine-readable marker: the pipeline caps coverage at REVIEW while this
    // is true, so assumed terms can never grant COVERED. policy_extract clears
    // it if it reads real clauses from the uploaded schedule.
    policy.clauses_assumed = true;
    assumedFields.push('clauses');
  }

  policy.assumed_fields = assumedFields;
  return { policy, assumedFields };
}

// Helper to construct ClaimState from frontend data
const constructClaimState = (body, files) => {
  const evidence = [];
  const documents = [];

  const photos = (files && files.photos) || [];
  const invoices = (files && files.invoices) || [];
  const govtIds = (files && files.govt_id) || [];
  const policyDocs = (files && files.policy_doc) || [];

  // Capture metadata sent by the app. Fall back to upload time / no-geo, and
  // record the fallback so downstream verification can account for it.
  const capturedAt = body.photo_captured_at || null;
  const lat = body.photo_lat !== undefined ? parseFloat(body.photo_lat) : NaN;
  const lon = body.photo_lon !== undefined ? parseFloat(body.photo_lon) : NaN;
  const geotag = (!isNaN(lat) && !isNaN(lon)) ? { lat, lon } : null;

  // digest -> set of roles it was uploaded under, for duplicate detection.
  const digestRoles = {};
  function noteDigest(digest, role) {
    (digestRoles[digest] = digestRoles[digest] || new Set()).add(role);
  }

  photos.forEach((file, index) => {
    noteDigest(sha256OfFile(file.path), 'photo');
    evidence.push({
      evidence_id: `IMG-${Date.now()}-${index}`,
      capture_stage: 'scene',
      file_ref: path.resolve(file.path),
      sha256: sha256OfFile(file.path),
      captured_at: capturedAt || new Date().toISOString(),
      geotag: geotag,
    });
  });

  const docSpecs = [
    { list: invoices, type: 'Invoice', prefix: 'DOC-INV', role: 'invoice' },
    { list: govtIds, type: 'GovtID', prefix: 'DOC-KYC', role: 'govt_id' },
    { list: policyDocs, type: 'PolicyDocument', prefix: 'DOC-POL', role: 'policy_doc' },
  ];
  docSpecs.forEach(({ list, type, prefix, role }) => {
    list.forEach((file, index) => {
      noteDigest(sha256OfFile(file.path), role);
      documents.push({
        document_id: `${prefix}-${Date.now()}-${index}`,
        document_type: type,
        file_ref: path.resolve(file.path),
        uploaded_at: new Date().toISOString(),
      });
    });
  });

  // Deterministic, un-gameable duplicate check: the SAME file (same SHA-256)
  // uploaded under two DIFFERENT roles is a substitution (e.g. one PDF used as
  // both policy and ID). Two genuinely different documents cannot collide.
  // NOTE: re-saving a JPEG with one pixel changed defeats SHA-256 entirely; the
  // triage classifier is the layered defence that still labels a re-saved menu a menu.
  const gateway_blocking_reasons = [];
  for (const [, roles] of Object.entries(digestRoles)) {
    const roleList = [...roles].filter((r) => r !== 'photo');
    if (roleList.length > 1) {
      gateway_blocking_reasons.push(
        'The same file was uploaded for more than one document (' + roleList.join(', ') +
        '). Please select the correct, separate file for each.'
      );
    }
  }
  // NOTE: the fabricated PremiumReceipt that used to be pushed here is gone. It
  // existed solely to satisfy an intake check the gateway itself guaranteed
  // could never fail — a control in name only. The intake node no longer gates
  // on it.

  // If the user confirmed vision-proposed items on the preview screen, build
  // line_items directly and mark the evidence as already processed so the
  // pipeline uses the confirmed items instead of re-running vision.
  const evidenceIds = evidence.map((e) => e.evidence_id);
  let lineItems = [];
  let visionProcessed = [];
  if (body.confirmed_items) {
    let confirmed = [];
    try {
      confirmed = JSON.parse(body.confirmed_items);
    } catch (e) {
      confirmed = [];
    }
    lineItems = confirmed.map((it, i) => ({
      item_ref: `LI-${i + 1}`,
      name: it.name || 'Unnamed item',
      description: it.description || '',
      category: it.category || '',
      quantity: it.quantity != null ? Number(it.quantity) : 1,
      serial_number: it.serial_number || null,
      evidence_refs: evidenceIds,
    }));
    if (lineItems.length > 0) {
      visionProcessed = evidenceIds;
    }
  }

  const { policy, assumedFields } = buildPolicy(body);

  const warnings = [];
  if (assumedFields.length > 0) {
    const hasPolicyDoc = policyDocs.length > 0;
    warnings.push(
      `Policy ${assumedFields.join(', ')} not supplied by the client; ` +
      (hasPolicyDoc
        ? 'attempting to read them from the uploaded policy schedule.'
        : 'no policy schedule was uploaded either, so generic terms were assumed.')
    );
  }
  if (!capturedAt) {
    warnings.push('Photo capture time not supplied by client; using upload time, which may fail the loss-window check.');
  }
  if (!geotag) {
    warnings.push('Photo geotag not supplied by client; geofence check skipped for this claim.');
  }

  return {
    policy: policy,
    event: {
      event_date: body.event_date || new Date().toISOString(),
      description: body.description || 'User reported flood damage.',
    },
    evidence: evidence,
    documents: documents,
    line_items: lineItems,
    vision_processed_evidence_ids: visionProcessed,
    gateway_blocking_reasons: gateway_blocking_reasons,
    warnings: warnings,
  };
};

// --- Endpoints ---

async function handleSubmit(category, req, res) {
  try {
    const grouped = groupFilesByField(req.files);
    // UX affordance mirroring the app's own requirement — NOT a safety control.
    // A curl client bypassing this still gets a REVIEW-capped pack (never
    // COVERED) because the pipeline enforces coverage independently.
    const missing = [];
    if (!grouped.policy_doc.length) missing.push('policy_doc');
    if (!grouped.govt_id.length) missing.push('govt_id');
    if (missing.length) {
      return res.status(400).json({ error: 'Required documents missing.', missing });
    }
    const claimState = constructClaimState(req.body, grouped);
    const response = await axios.post(`${PYTHON_BACKEND_URL}/api/v1/claim/submit`, claimState);
    res.json({ success: true, category, data: response.data });
  } catch (error) {
    console.error(`Pipeline Error (${category}):`, error.message);
    res.status(500).json({ error: 'Failed to submit claim to the pipeline.' });
  }
}

app.post('/api/personal/submit', CLAIM_UPLOAD_FIELDS, (req, res) => handleSubmit('personal', req, res));
app.post('/api/commercial/submit', CLAIM_UPLOAD_FIELDS, (req, res) => handleSubmit('commercial', req, res));
app.post('/api/insurance/submit', CLAIM_UPLOAD_FIELDS, (req, res) => handleSubmit('insurance', req, res));

// Vision preview: single image -> proposed line items for user confirmation.
// upload.any() (not .single) so an unexpected field name never throws.
app.post('/api/preview', upload.any(), async (req, res) => {
  try {
    const files = req.files || [];
    const file = files.find((f) => f.fieldname === 'photo') || files[0];
    if (!file) {
      return res.status(400).json({ error: 'No photo supplied.' });
    }
    const buffer = fs.readFileSync(file.path);
    const payload = {
      image_base64: buffer.toString('base64'),
      mime_type: file.mimetype || 'image/jpeg',
      capture_stage: req.body.capture_stage || 'scene',
      declared_asset_categories: mapCategories(req.body.categories),
    };
    const response = await axios.post(`${PYTHON_BACKEND_URL}/api/v1/vision/preview`, payload);
    res.json({ success: true, data: response.data });
  } catch (error) {
    const status = error.response ? error.response.status : 500;
    const detail = error.response && error.response.data ? error.response.data : { error: error.message };
    console.error('Preview Error:', error.message);
    res.status(status).json(detail);
  }
});

// Generic Status endpoint to proxy to LangGraph
app.get('/api/claim/:claim_id', async (req, res) => {
  try {
    const response = await axios.get(`${PYTHON_BACKEND_URL}/api/v1/claim/${req.params.claim_id}`);
    res.json(response.data);
  } catch (error) {
    const status = error.response ? error.response.status : 500;
    res.status(status).json({ error: 'Failed to fetch claim status.' });
  }
});

app.get('/', (req, res) => {
  res.send({ status: "online", service: "SurakshaDraft API Gateway", message: "Use the /api endpoints to submit claims." });
});

// Error-handling middleware: a malformed upload (e.g. a MulterError) must
// return a clean 4xx to the client, never crash the process with an unhandled
// error. Must be registered last, after all routes.
app.use((err, req, res, next) => {
  if (err instanceof multer.MulterError) {
    console.error('Upload error:', err.code, err.field);
    return res.status(400).json({ error: `Upload error: ${err.message} (field "${err.field}")` });
  }
  if (err) {
    console.error('Unhandled error:', err.message);
    return res.status(500).json({ error: 'Internal server error.' });
  }
  next();
});

app.listen(port, () => {
  console.log(`Express Backend running on http://localhost:${port}`);
});

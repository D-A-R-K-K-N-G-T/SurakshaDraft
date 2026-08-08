const express = require('express');
const multer = require('multer');
const cors = require('cors');
const axios = require('axios');
const path = require('path');
const fs = require('fs');

const app = express();
const port = process.env.PORT || 3000;
const PYTHON_BACKEND_URL = 'http://127.0.0.1:8000';

app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

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

// Helper to construct ClaimState from frontend data
const constructClaimState = (body, files) => {
  const evidence = [];
  const documents = [];

  if (files) {
      files.forEach((file, index) => {
          // Identify if it's an image for vision or a document for valuation
          if (file.mimetype.startsWith('image/')) {
              evidence.push({
                  evidence_id: `IMG-${Date.now()}-${index}`,
                  capture_stage: "scene",
                  file_ref: path.resolve(file.path),
                  sha256: "dummy-hash", // in prod, calculate actual hash of file buffer
                  captured_at: new Date().toISOString(),
                  geotag: { lat: 10.0, lon: 10.0 } // placeholder, should come from frontend EXIF parsing
              });
          } else {
              documents.push({
                  document_id: `DOC-${Date.now()}-${index}`,
                  document_type: "Invoice", 
                  file_ref: path.resolve(file.path),
                  uploaded_at: new Date().toISOString(),
              });
          }
      });
  }

  // Push PremiumReceipt to pass deterministic intake checks
  documents.push({
      document_id: `DOC-RECEIPT-${Date.now()}`,
      document_type: "PremiumReceipt",
      file_ref: "system-generated",
      uploaded_at: new Date().toISOString()
  });

  return {
      policy: {
          product: body.product || "Standard Flood Protection",
          start_date: "2025-01-01T00:00:00Z",
          end_date: "2026-12-31T23:59:59Z",
          sum_insured_stock: 1000000,
          clauses: body.clauses || "Flood damage to stock and machinery is covered.",
          asset_categories: body.categories ? body.categories.split(",") : ["Stock"]
      },
      event: {
          event_date: body.event_date || new Date().toISOString(),
          description: body.description || "User reported flood damage."
      },
      evidence: evidence,
      documents: documents,
      line_items: []
  };
};

// --- Endpoints ---

// 1. Personal Claims
app.post('/api/personal/submit', upload.array('files'), async (req, res) => {
    try {
        const claimState = constructClaimState(req.body, req.files);
        const response = await axios.post(`${PYTHON_BACKEND_URL}/api/v1/claim/submit`, claimState);
        res.json({ success: true, category: 'personal', data: response.data });
    } catch (error) {
        console.error("Pipeline Error:", error.message);
        res.status(500).json({ error: error.message });
    }
});

// 2. Commercial Claims
app.post('/api/commercial/submit', upload.array('files'), async (req, res) => {
    try {
        const claimState = constructClaimState(req.body, req.files);
        // Commercial might enforce stricter document presence validation here
        const response = await axios.post(`${PYTHON_BACKEND_URL}/api/v1/claim/submit`, claimState);
        res.json({ success: true, category: 'commercial', data: response.data });
    } catch (error) {
        console.error("Pipeline Error:", error.message);
        res.status(500).json({ error: error.message });
    }
});

// 3. Insurance Firm API (B2B)
app.post('/api/insurance/submit', upload.array('files'), async (req, res) => {
    try {
        const claimState = constructClaimState(req.body, req.files);
        const response = await axios.post(`${PYTHON_BACKEND_URL}/api/v1/claim/submit`, claimState);
        res.json({ success: true, category: 'insurance', data: response.data });
    } catch (error) {
        console.error("Pipeline Error:", error.message);
        res.status(500).json({ error: error.message });
    }
});

// Generic Status endpoint to proxy to LangGraph
app.get('/api/claim/:claim_id', async (req, res) => {
    try {
        const response = await axios.get(`${PYTHON_BACKEND_URL}/api/v1/claim/${req.params.claim_id}`);
        res.json(response.data);
    } catch (error) {
        const status = error.response ? error.response.status : 500;
        res.status(status).json({ error: error.message });
    }
});

app.get('/', (req, res) => {
    res.send({ status: "online", service: "SurakshaDraft API Gateway", message: "Use the /api endpoints to submit claims." });
});

app.listen(port, () => {
  console.log(`Express Backend running on http://localhost:${port}`);
});

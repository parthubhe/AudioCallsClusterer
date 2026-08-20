# 🎧 Audio Call Clusterer

A web application that discovers patterns in telecom operator announcements by finding near-exact waveform duplicates. Paste S3 URLs or upload an Excel file, and the tool clusters audio files using duration bucketing, PCM hash matching, and MFCC similarity analysis.

![Stack](https://img.shields.io/badge/React-Vite-blue) ![Stack](https://img.shields.io/badge/FastAPI-Python-green)

## Features

- **Two input modes** — Paste S3 URLs directly or drag-and-drop an Excel file (`.xlsx`)
- **Two-tier clustering** — Tier 1: exact PCM hash matching, Tier 2: MFCC-based perceptual similarity
- **Duration bucketing** — Groups files by duration before comparing, avoiding O(n²) comparisons
- **Real-time progress** — SSE-powered progress bar shows download, extraction, and clustering phases
- **Interactive cluster cards** — Click audio bubbles to play, with a playing-state ring animation
- **Cluster labeling** — Assign labels to clusters (e.g., "Not Reachable — Hindi") with backend persistence
- **Audio player** — Seekable progress bar, play/pause, download, and copy URL
- **CSV export** — Download clustering results with labels as a CSV file

## Tech Stack

| Layer    | Technology                        |
|----------|-----------------------------------|
| Frontend | React 19, Vite 8, Tailwind CSS 4, Framer Motion |
| Backend  | FastAPI, Uvicorn, aiohttp         |
| Audio    | librosa, scipy, numpy, wave       |
| Data     | pandas, scikit-learn              |

## Prerequisites

- **Python 3.11+**
- **Node.js 18+**
- **npm**

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/parthubhe/AudioCallsClusterer.git
cd AudioCallsClusterer
```

### 2. Backend setup

```bash
cd backend

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install fastapi uvicorn aiohttp pandas openpyxl librosa scikit-learn scipy numpy soundfile sse-starlette python-multipart

# Start the backend server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Frontend setup

```bash
cd frontend

# Install dependencies
npm install

# Start the dev server
npm run dev
```

### 4. Open the app

Navigate to [http://localhost:5173](http://localhost:5173) in your browser.

## Usage

1. **Paste URLs** — Switch to "Paste URLs" mode, paste S3 `.wav` URLs (one per line), and click **Cluster Audio**.
2. **Upload Excel** — Switch to "Upload Excel" mode, drag-and-drop an `.xlsx` file containing a `RecordingURL` column.
3. **Review clusters** — Click audio bubbles to play clips. Assign labels to each cluster.
4. **Export** — Click **Download CSV** to export results with `url`, `cluster_id`, `cluster_size`, and `label` columns.

## API Endpoints

| Method | Endpoint                    | Description                          |
|--------|-----------------------------|--------------------------------------|
| POST   | `/api/cluster`              | Cluster URLs (synchronous)           |
| POST   | `/api/cluster-stream`       | Cluster URLs with SSE progress       |
| POST   | `/api/upload-excel`         | Upload Excel (synchronous)           |
| POST   | `/api/upload-excel-stream`  | Upload Excel with SSE progress       |
| POST   | `/api/labels/{job_id}`      | Save cluster labels                  |
| GET    | `/api/labels/{job_id}`      | Get cluster labels                   |
| GET    | `/api/export/{job_id}`      | Download results as CSV              |
| GET    | `/api/audio-proxy?url=...`  | Proxy/download cached audio file     |

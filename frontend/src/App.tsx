import { useState, useCallback } from 'react';
import { FileAudio, UploadCloud, RefreshCw, Download, FileSpreadsheet } from 'lucide-react';
import { motion } from 'framer-motion';
import ClusterGraph from './components/ClusterGraph';
import AudioPlayer from './components/AudioPlayer';
import ProgressBar from './components/ProgressBar';
import ExcelUpload from './components/ExcelUpload';

interface ProgressState {
  phase: string;
  current: number;
  total: number;
  message: string;
}

export default function App() {
  const [urls, setUrls] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [clusters, setClusters] = useState<string[][] | null>(null);
  const [selectedAudio, setSelectedAudio] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [progress, setProgress] = useState<ProgressState | null>(null);
  const [excelFile, setExcelFile] = useState<File | null>(null);
  const [inputMode, setInputMode] = useState<'paste' | 'excel'>('paste');
  const [errors, setErrors] = useState<any[]>([]);

  const processWithSSE = useCallback(async (url: string, body: BodyInit, headers?: HeadersInit) => {
    setLoading(true);
    setClusters(null);
    setSelectedAudio(null);
    setJobId(null);
    setLabels({});
    setErrors([]);
    setProgress({ phase: 'starting', current: 0, total: 1, message: 'Starting...' });

    try {
      const response = await fetch(url, {
        method: 'POST',
        body,
        headers: headers as Record<string, string>,
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(err.detail || 'Failed to process');
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      if (!reader) throw new Error('No response body');

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data:')) {
            const dataStr = line.slice(5).trim();
            if (dataStr === 'ping') continue;

            try {
              const data = JSON.parse(dataStr);

              // Check if this is a progress event or the final result
              if (data.phase) {
                setProgress(data);
              } else if (data.clusters) {
                // Final result
                setClusters(data.clusters);
                setJobId(data.job_id);
                setErrors(data.errors || []);
                setProgress({ phase: 'done', current: data.clusters.length, total: data.clusters.length, message: `Done! ${data.clusters.length} clusters found.` });
              }
            } catch {
              // ignore non-JSON lines
            }
          }
        }
      }
    } catch (err: any) {
      console.error(err);
      alert(err.message || 'Error processing audio files. Make sure the backend is running.');
      setProgress(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleProcess = async () => {
    if (inputMode === 'excel' && excelFile) {
      const formData = new FormData();
      formData.append('file', excelFile);
      await processWithSSE('http://localhost:8000/api/upload-excel-stream', formData);
    } else if (inputMode === 'paste' && urls.trim()) {
      await processWithSSE(
        'http://localhost:8000/api/cluster-stream',
        JSON.stringify({ text: urls }),
        { 'Content-Type': 'application/json' }
      );
    }
  };

  const handleLabelChange = async (clusterIdx: number, label: string) => {
    const newLabels = { ...labels, [String(clusterIdx)]: label };
    setLabels(newLabels);

    // Persist to backend
    if (jobId) {
      try {
        await fetch(`http://localhost:8000/api/labels/${jobId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ labels: newLabels }),
        });
      } catch (err) {
        console.error('Failed to save labels:', err);
      }
    }
  };

  const handleExport = async () => {
    if (!jobId) return;
    try {
      const response = await fetch(`http://localhost:8000/api/export/${jobId}`);
      if (!response.ok) throw new Error('Export failed');
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `clusters_${jobId}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Export failed:', err);
      alert('Failed to export results.');
    }
  };

  const canProcess = inputMode === 'paste' ? urls.trim().length > 0 : excelFile !== null;

  return (
    <div className="min-h-screen bg-[#0b0c10] text-gray-200 p-6 md:p-8 flex flex-col font-sans">
      <header className="mb-10 text-center">
        <h1 className="text-4xl font-extrabold bg-gradient-to-r from-cyan-400 to-blue-500 bg-clip-text text-transparent flex items-center justify-center gap-3">
          <FileAudio className="text-cyan-400" size={40} />
          Audio Call Clusterer
        </h1>
        <p className="text-gray-500 mt-2 max-w-xl mx-auto text-sm">
          Discover patterns in operator announcements by finding near-exact waveform duplicates.
        </p>
      </header>

      <main className="flex-1 max-w-7xl mx-auto w-full grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-6">
        
        {/* Input Panel */}
        <div className="glass p-5 rounded-2xl flex flex-col gap-4">
          {/* Mode toggle */}
          <div className="flex bg-white/5 rounded-xl p-1 gap-1">
            <button
              onClick={() => setInputMode('paste')}
              className={`flex-1 flex items-center justify-center gap-1.5 py-2 px-3 rounded-lg text-xs font-semibold transition-all ${
                inputMode === 'paste'
                  ? 'bg-cyan-500/20 text-cyan-300 shadow-sm'
                  : 'text-gray-500 hover:text-gray-400'
              }`}
            >
              <UploadCloud size={14} />
              Paste URLs
            </button>
            <button
              onClick={() => setInputMode('excel')}
              className={`flex-1 flex items-center justify-center gap-1.5 py-2 px-3 rounded-lg text-xs font-semibold transition-all ${
                inputMode === 'excel'
                  ? 'bg-cyan-500/20 text-cyan-300 shadow-sm'
                  : 'text-gray-500 hover:text-gray-400'
              }`}
            >
              <FileSpreadsheet size={14} />
              Upload Excel
            </button>
          </div>

          {/* Input area */}
          {inputMode === 'paste' ? (
            <textarea
              className="w-full bg-white/5 border border-white/10 rounded-xl p-4 text-sm text-gray-300 focus:outline-none focus:ring-2 focus:ring-cyan-500/50 resize-none flex-1 min-h-[260px] transition-all placeholder-gray-600"
              placeholder={"Paste S3 URLs here, one per line...\nhttps://s3.amazonaws.com/...\nhttps://s3.amazonaws.com/..."}
              value={urls}
              onChange={(e) => setUrls(e.target.value)}
              disabled={loading}
            />
          ) : (
            <div className="flex-1 flex flex-col justify-center">
              <ExcelUpload
                onFileSelected={(file) => setExcelFile(file)}
                disabled={loading}
              />
            </div>
          )}

          {/* Progress */}
          {progress && loading && (
            <ProgressBar
              phase={progress.phase}
              current={progress.current}
              total={progress.total}
              message={progress.message}
            />
          )}

          {/* Process button */}
          <button
            onClick={handleProcess}
            disabled={loading || !canProcess}
            className="w-full bg-cyan-500 hover:bg-cyan-400 text-[#0b0c10] font-bold py-3 px-6 rounded-xl transition-all disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2 shadow-[0_0_20px_rgba(34,211,238,0.15)] hover:shadow-[0_0_30px_rgba(34,211,238,0.3)] active:scale-[0.98]"
          >
            {loading ? <RefreshCw className="animate-spin" size={18} /> : <FileAudio size={18} />}
            {loading ? 'Processing...' : 'Cluster Audio'}
          </button>

          {/* Export button */}
          {clusters && jobId && (
            <button
              onClick={handleExport}
              className="w-full bg-white/5 hover:bg-white/10 border border-white/10 hover:border-white/20 text-gray-300 font-semibold py-2.5 px-6 rounded-xl transition-all flex items-center justify-center gap-2"
            >
              <Download size={16} />
              Download CSV
            </button>
          )}

          {/* Error summary */}
          {errors.length > 0 && (
            <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-3">
              <p className="text-xs text-red-400 font-semibold mb-1">⚠ {errors.length} download error{errors.length > 1 ? 's' : ''}</p>
              <p className="text-[10px] text-red-400/60">Some files couldn't be downloaded. They won't appear in clusters.</p>
            </div>
          )}
        </div>

        {/* Visualization Panel */}
        <div className="glass rounded-2xl p-5 flex flex-col relative min-h-[500px]">
          {clusters ? (
            <>
              <div className="flex justify-between items-center mb-4">
                <h2 className="text-lg font-bold">Identified Clusters</h2>
                <span className="text-xs font-medium text-gray-400 bg-white/10 px-3 py-1 rounded-full">
                  {clusters.length} groups
                </span>
              </div>
              <div className="flex-1 bg-black/20 rounded-xl border border-white/5 overflow-hidden">
                <ClusterGraph
                  clusters={clusters}
                  labels={labels}
                  onSelectAudio={setSelectedAudio}
                  onLabelChange={handleLabelChange}
                  playingUrl={selectedAudio}
                />
              </div>
            </>
          ) : progress && loading ? (
            <div className="flex-1 flex flex-col items-center justify-center text-gray-500 gap-6">
              <div className="relative">
                <motion.div
                  className="w-20 h-20 rounded-full border-2 border-cyan-500/30 flex items-center justify-center"
                  animate={{ rotate: 360 }}
                  transition={{ duration: 3, repeat: Infinity, ease: 'linear' }}
                >
                  <FileAudio size={32} className="text-cyan-500/50" />
                </motion.div>
                <motion.span
                  className="absolute inset-0 rounded-full border-2 border-cyan-400/40"
                  animate={{ scale: [1, 1.3], opacity: [0.5, 0] }}
                  transition={{ duration: 1.5, repeat: Infinity, ease: 'easeOut' }}
                />
              </div>
              <p className="text-sm text-gray-500">{progress.message}</p>
            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-gray-500 gap-4">
              <div className="w-24 h-24 rounded-full border border-dashed border-gray-700 flex items-center justify-center">
                <FileAudio size={40} className="opacity-30" />
              </div>
              <p className="text-sm text-gray-600">Paste URLs or upload an Excel file to get started.</p>
            </div>
          )}
        </div>
      </main>

      {/* Audio Player Panel */}
      {selectedAudio && (
        <AudioPlayer src={selectedAudio} onClose={() => setSelectedAudio(null)} />
      )}
    </div>
  );
}


import { useState, useCallback, useRef } from 'react';
import { FileAudio, UploadCloud, RefreshCw, Download, FileSpreadsheet, Database, ChevronRight, ChevronLeft, Bookmark, Save, Upload } from 'lucide-react';
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

interface BatchInfo {
  totalFiles: number;
  currentOffset: number;
  batchSize: number;
  currentBatchNum: number;
  totalBatches: number;
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
  const [inputMode, setInputMode] = useState<'paste' | 'excel' | 'batch' | 'saved'>('paste');
  const [enableCallerTune, setEnableCallerTune] = useState(false);
  const [errors, setErrors] = useState<any[]>([]);

  // Batch processing state
  const [batchSize, setBatchSize] = useState(5000);
  const [batchInfo, setBatchInfo] = useState<BatchInfo | null>(null);
  const [cacheTotal, setCacheTotal] = useState<number | null>(null);
  const [loadingCache, setLoadingCache] = useState(false);
  const [allBatchClusters, setAllBatchClusters] = useState<string[][]>([]);
  const allBatchLabelsRef = useRef<Record<string, string>>({});
  
  // Saved pages state
  const [savedBatches, setSavedBatches] = useState<string[]>([]);
  const [viewingBatchName, setViewingBatchName] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

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

              if (data.phase) {
                setProgress(data);
              } else if (data.clusters) {
                setClusters(data.clusters);
                setJobId(data.job_id);
                setLabels(data.labels || {});
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

  // Batch SSE handler — accumulates clusters across batches
  const processBatchSSE = useCallback(async (offset: number, size: number, isFirstBatch: boolean) => {
    setLoading(true);
    setSelectedAudio(null);
    setProgress({ phase: 'starting', current: 0, total: 1, message: 'Starting batch...' });

    if (isFirstBatch) {
      setAllBatchClusters([]);
      allBatchLabelsRef.current = {};
      setClusters(null);
      setJobId(null);
      setLabels({});
      setErrors([]);
    }

    try {
      const response = await fetch('http://localhost:8000/api/cluster-batch-stream', {
        method: 'POST',
        body: JSON.stringify({ batch_size: size, offset, enable_caller_tune: enableCallerTune }),
        headers: { 'Content-Type': 'application/json' },
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(err.detail || 'Failed to process batch');
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

              if (data.phase) {
                setProgress(data);
              } else if (data.clusters) {
                // Accumulate clusters from this batch
                setAllBatchClusters(prev => {
                  const merged = [...prev, ...data.clusters];
                  setClusters(merged);
                  return merged;
                });
                
                // Merge labels
                const prev = allBatchLabelsRef.current;
                const prevCount = Object.keys(prev).length > 0 ? Math.max(...Object.keys(prev).map(Number)) + 1 : 0;
                const newLabels: Record<string, string> = { ...prev };
                if (data.labels) {
                  for (const [key, val] of Object.entries(data.labels)) {
                    newLabels[String(Number(key) + (isFirstBatch ? 0 : prevCount))] = val as string;
                  }
                }
                allBatchLabelsRef.current = newLabels;
                setLabels(newLabels);

                setJobId(data.job_id);
                setErrors(prev => [...prev, ...(data.errors || [])]);

                const totalFiles = data.total_files;
                const newOffset = offset + size;
                const totalBatches = Math.ceil(totalFiles / size);
                const currentBatch = Math.floor(offset / size) + 1;
                
                setBatchInfo({
                  totalFiles,
                  currentOffset: newOffset,
                  batchSize: size,
                  currentBatchNum: currentBatch,
                  totalBatches,
                });
                setCacheTotal(totalFiles);

                setProgress({
                  phase: 'done',
                  current: data.clusters.length,
                  total: data.clusters.length,
                  message: `Batch ${currentBatch}/${totalBatches} done! ${data.clusters.length} clusters in this batch.`,
                });
              }
            } catch {
              // ignore
            }
          }
        }
      }
    } catch (err: any) {
      console.error(err);
      alert(err.message || 'Error processing batch.');
      setProgress(null);
    } finally {
      setLoading(false);
    }
  }, [enableCallerTune]);

  const handleProcess = async () => {
    if (inputMode === 'excel' && excelFile) {
      const formData = new FormData();
      formData.append('file', excelFile);
      formData.append('enable_caller_tune', enableCallerTune ? 'true' : 'false');
      await processWithSSE('http://localhost:8000/api/upload-excel-stream', formData);
    } else if (inputMode === 'paste' && urls.trim()) {
      await processWithSSE(
        'http://localhost:8000/api/cluster-stream',
        JSON.stringify({ text: urls, enable_caller_tune: enableCallerTune }),
        { 'Content-Type': 'application/json' }
      );
    } else if (inputMode === 'batch') {
      await processBatchSSE(0, batchSize, true);
    }
  };

  const handleNextBatch = async () => {
    if (!batchInfo) return;
    if (batchInfo.currentOffset >= batchInfo.totalFiles) return;
    await processBatchSSE(batchInfo.currentOffset, batchInfo.batchSize, false);
  };

  const handlePrevBatch = async () => {
    if (!batchInfo) return;
    await processBatchSSE(0, batchInfo.batchSize, true);
  };

  const fetchCacheInfo = async () => {
    setLoadingCache(true);
    try {
      const res = await fetch('http://localhost:8000/api/cache-files');
      const data = await res.json();
      setCacheTotal(data.total_cached_files || data.total);
    } catch (err) {
      console.error('Failed to fetch cache info:', err);
      setCacheTotal(0);
    } finally {
      setLoadingCache(false);
    }
  };

  const processImportSSE = async (file: File) => {
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      if (!parsed.clusters || !Array.isArray(parsed.clusters)) {
        alert("Invalid JSON format. Must contain a 'clusters' array.");
        return;
      }
      
      const defaultName = file.name.replace('.json', '');
      const name = prompt("Enter a name for this imported batch:", defaultName) || defaultName;

      const payload = {
        name,
        clusters: parsed.clusters,
        labels: parsed.labels || {}
      };

      setLoading(true);
      setClusters(null);
      setProgress({ phase: 'starting', current: 0, total: 1, message: 'Starting import...' });

      const response = await fetch('http://localhost:8000/api/import-batch-stream', {
        method: 'POST',
        body: JSON.stringify(payload),
        headers: { 'Content-Type': 'application/json' },
      });

      if (!response.ok) {
        throw new Error('Failed to import batch');
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
              if (data.phase) {
                setProgress(data);
              } else if (data.batch_name) {
                setProgress({ phase: 'done', current: 1, total: 1, message: data.message });
                // We're done downloading!
                await fetchSavedBatches();
                await loadSavedBatch(data.batch_name);
                setInputMode('saved');
              }
            } catch {}
          }
        }
      }
    } catch (err: any) {
      console.error(err);
      alert(err.message || "Failed to import");
    } finally {
      setLoading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    processImportSSE(file);
  };

  const fetchSavedBatches = async () => {
    try {
      const res = await fetch('http://localhost:8000/api/saved-batches');
      const data = await res.json();
      setSavedBatches(data.batches);
    } catch (err) {
      console.error('Failed to fetch saved batches:', err);
    }
  };

  const loadSavedBatch = async (name: string) => {
    setLoading(true);
    setClusters(null);
    setViewingBatchName(name);
    try {
      const res = await fetch(`http://localhost:8000/api/saved-batches/${name}`);
      if (!res.ok) throw new Error('Failed to load batch');
      const data = await res.json();
      setClusters(data.clusters);
      setLabels(data.labels || {});
      setJobId(name);
    } catch (err) {
      alert(err);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveBatch = async () => {
    if (!clusters) return;
    const name = prompt("Enter a name for this batch:", `batch_${new Date().getTime()}`);
    if (!name) return;
    
    try {
      const res = await fetch('http://localhost:8000/api/saved-batches', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, clusters, labels })
      });
      if (res.ok) {
        alert('Saved successfully!');
        if (inputMode === 'saved') fetchSavedBatches();
      } else {
        alert('Failed to save');
      }
    } catch (err) {
      alert(err);
    }
  };

  const handleLabelChange = async (clusterIdx: number, label: string) => {
    const newLabels = { ...labels, [String(clusterIdx)]: label };
    setLabels(newLabels);

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

  const canProcess = inputMode === 'paste'
    ? urls.trim().length > 0
    : inputMode === 'excel'
      ? excelFile !== null
      : cacheTotal !== null && cacheTotal > 0;

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
            <button
              onClick={() => {
                setInputMode('batch');
                if (cacheTotal === null) fetchCacheInfo();
              }}
              className={`flex-1 flex items-center justify-center gap-1.5 py-2 px-3 rounded-lg text-xs font-semibold transition-all ${
                inputMode === 'batch'
                  ? 'bg-amber-500/20 text-amber-300 shadow-sm'
                  : 'text-gray-500 hover:text-gray-400'
              }`}
            >
              <Database size={14} />
              Batch Cache
            </button>
            <button
              onClick={() => {
                setInputMode('saved');
                fetchSavedBatches();
              }}
              className={`flex-1 flex items-center justify-center gap-1.5 py-2 px-3 rounded-lg text-xs font-semibold transition-all ${
                inputMode === 'saved'
                  ? 'bg-purple-500/20 text-purple-300 shadow-sm'
                  : 'text-gray-500 hover:text-gray-400'
              }`}
            >
              <Bookmark size={14} />
              Saved Pages
            </button>
          </div>

          {/* Caller Tune Toggle */}
          <div className="flex items-center justify-between p-4 bg-white/5 rounded-xl border border-white/10">
            <div>
              <h3 className="text-sm font-medium text-gray-200">Caller Tune Clustering</h3>
              <p className="text-xs text-gray-500 mt-0.5">Use AI to group all music/songs</p>
            </div>
            <label className="relative inline-flex items-center cursor-pointer">
              <input 
                type="checkbox" 
                className="sr-only peer" 
                checked={enableCallerTune}
                onChange={(e) => setEnableCallerTune(e.target.checked)}
              />
              <div className="w-11 h-6 bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-cyan-600"></div>
            </label>
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
          ) : inputMode === 'excel' ? (
            <div className="flex-1 flex flex-col justify-center">
              <ExcelUpload
                onFileSelected={(file) => setExcelFile(file)}
                disabled={loading}
              />
            </div>
          ) : inputMode === 'batch' ? (
            /* Batch from Cache panel */
            <div className="flex-1 flex flex-col gap-4">
              {/* Cache status */}
              <div className="bg-amber-500/10 border border-amber-500/20 rounded-xl p-4">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-sm font-semibold text-amber-300 flex items-center gap-2">
                    <Database size={16} />
                    Audio Cache
                  </h3>
                  <button
                    onClick={fetchCacheInfo}
                    disabled={loadingCache}
                    className="text-xs text-amber-400 hover:text-amber-300 transition-colors"
                  >
                    {loadingCache ? 'Scanning...' : 'Refresh'}
                  </button>
                </div>
                {cacheTotal !== null ? (
                  <p className="text-2xl font-bold text-amber-200">{cacheTotal.toLocaleString()} <span className="text-sm font-normal text-amber-400">files cached</span></p>
                ) : (
                  <p className="text-sm text-gray-500">Click Refresh to scan cache...</p>
                )}
              </div>

              {/* Batch size selector */}
              <div className="bg-white/5 rounded-xl p-4 border border-white/10">
                <label className="text-xs font-medium text-gray-400 block mb-2">Batch Size</label>
                <div className="grid grid-cols-4 gap-2">
                  {[1000, 2000, 5000, 10000].map(size => (
                    <button
                      key={size}
                      onClick={() => setBatchSize(size)}
                      className={`py-2 rounded-lg text-xs font-bold transition-all ${
                        batchSize === size
                          ? 'bg-amber-500/30 text-amber-200 border border-amber-500/50'
                          : 'bg-white/5 text-gray-500 border border-white/10 hover:border-white/20 hover:text-gray-400'
                      }`}
                    >
                      {(size / 1000).toFixed(0)}k
                    </button>
                  ))}
                </div>
                {cacheTotal !== null && (
                  <p className="text-[10px] text-gray-600 mt-2">
                    {Math.ceil(cacheTotal / batchSize)} batch{Math.ceil(cacheTotal / batchSize) > 1 ? 'es' : ''} of {batchSize.toLocaleString()} files each
                  </p>
                )}
              </div>

              {/* Batch navigation */}
              {batchInfo && !loading && (
                <div className="flex items-center gap-2">
                  <button
                    onClick={handlePrevBatch}
                    disabled={loading || batchInfo.currentBatchNum <= 1}
                    className="flex-1 flex items-center justify-center gap-1 py-2.5 rounded-xl text-xs font-semibold bg-white/5 border border-white/10 text-gray-400 hover:bg-white/10 hover:text-gray-300 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    <ChevronLeft size={14} />
                    Restart
                  </button>
                  <div className="text-center text-xs text-gray-500 px-2">
                    <span className="text-amber-300 font-bold">{batchInfo.currentBatchNum}</span>
                    <span className="mx-1">/</span>
                    <span>{batchInfo.totalBatches}</span>
                  </div>
                  <button
                    onClick={handleNextBatch}
                    disabled={loading || batchInfo.currentOffset >= batchInfo.totalFiles}
                    className="flex-1 flex items-center justify-center gap-1 py-2.5 rounded-xl text-xs font-semibold bg-amber-500/20 border border-amber-500/40 text-amber-300 hover:bg-amber-500/30 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    Next Batch
                    <ChevronRight size={14} />
                  </button>
                </div>
              )}
            </div>
          ) : (
            /* Saved Pages panel */
            <div className="flex-1 flex flex-col gap-4">
              <div className="bg-purple-500/10 border border-purple-500/20 rounded-xl p-4">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-sm font-semibold text-purple-300 flex items-center gap-2">
                    <Bookmark size={16} />
                    Saved Batches
                  </h3>
                  <div className="flex items-center gap-3">
                    <input
                      type="file"
                      accept=".json"
                      ref={fileInputRef}
                      onChange={handleImportFile}
                      className="hidden"
                    />
                    <button
                      onClick={() => fileInputRef.current?.click()}
                      className="text-xs text-purple-400 hover:text-purple-300 transition-colors flex items-center gap-1"
                    >
                      <Upload size={14} />
                      Import
                    </button>
                    <button
                      onClick={fetchSavedBatches}
                      className="text-xs text-purple-400 hover:text-purple-300 transition-colors"
                    >
                      Refresh
                    </button>
                  </div>
                </div>
                {savedBatches.length > 0 ? (
                  <div className="mt-4 flex flex-col gap-2 max-h-[300px] overflow-y-auto">
                    {savedBatches.map(name => (
                      <button
                        key={name}
                        onClick={() => loadSavedBatch(name)}
                        className="text-left px-3 py-2 bg-white/5 hover:bg-white/10 rounded-lg text-sm text-gray-300 border border-white/5 transition-all"
                      >
                        {name}
                      </button>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-gray-500 mt-2">No saved batches found.</p>
                )}
              </div>
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
            className={`w-full font-bold py-3 px-6 rounded-xl transition-all disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2 active:scale-[0.98] ${
              inputMode === 'batch'
                ? 'bg-amber-500 hover:bg-amber-400 text-[#0b0c10] shadow-[0_0_20px_rgba(245,158,11,0.15)] hover:shadow-[0_0_30px_rgba(245,158,11,0.3)]'
                : 'bg-cyan-500 hover:bg-cyan-400 text-[#0b0c10] shadow-[0_0_20px_rgba(34,211,238,0.15)] hover:shadow-[0_0_30px_rgba(34,211,238,0.3)]'
            }`}
          >
            {loading ? <RefreshCw className="animate-spin" size={18} /> : inputMode === 'batch' ? <Database size={18} /> : inputMode === 'saved' ? <Bookmark size={18} /> : <FileAudio size={18} />}
            {loading ? 'Processing...' : inputMode === 'batch' ? `Process Batch (${batchSize.toLocaleString()} files)` : inputMode === 'saved' ? 'Load a Saved Page' : 'Cluster Audio'}
          </button>

          {/* Export and Save buttons */}
          {clusters && jobId && (
            <div className="flex gap-2">
              <button
                onClick={handleExport}
                className="flex-1 bg-white/5 hover:bg-white/10 border border-white/10 hover:border-white/20 text-gray-300 font-semibold py-2.5 px-4 rounded-xl transition-all flex items-center justify-center gap-2 text-sm"
              >
                <Download size={16} />
                Download CSV
              </button>
              <button
                onClick={handleSaveBatch}
                className="flex-1 bg-purple-500/20 hover:bg-purple-500/30 border border-purple-500/30 text-purple-300 font-semibold py-2.5 px-4 rounded-xl transition-all flex items-center justify-center gap-2 text-sm"
              >
                <Save size={16} />
                Save Result
              </button>
            </div>
          )}

          {/* Error summary */}
          {errors.length > 0 && (
            <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-3">
              <p className="text-xs text-red-400 font-semibold mb-1">⚠ {errors.length} download error{errors.length > 1 ? 's' : ''}</p>
              <p className="text-[10px] text-red-400/60">Some files couldn't be downloaded. They won't appear in clusters.</p>
            </div>
          )}

          {/* Batch accumulation summary */}
          {inputMode === 'batch' && allBatchClusters.length > 0 && !loading && (
            <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-xl p-3">
              <p className="text-xs text-emerald-400 font-semibold mb-1">
                📊 {allBatchClusters.length} clusters accumulated
              </p>
              <p className="text-[10px] text-emerald-400/60">
                Across {batchInfo?.currentBatchNum || 1} batch{(batchInfo?.currentBatchNum || 1) > 1 ? 'es' : ''}
              </p>
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
                  jobId={jobId}
                  batchName={viewingBatchName || jobId}
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


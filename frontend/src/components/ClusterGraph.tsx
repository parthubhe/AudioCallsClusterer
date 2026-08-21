import { useState } from 'react';
import { motion } from 'framer-motion';
import { Tag, Check, Play } from 'lucide-react';

interface ClusterGraphProps {
  clusters: string[][];
  labels: Record<string, string>;
  onSelectAudio: (url: string) => void;
  onLabelChange: (clusterIdx: number, label: string) => void;
  playingUrl: string | null;
  jobId?: string | null;
  batchName?: string | null;
}

export default function ClusterGraph({ clusters, labels, onSelectAudio, onLabelChange, playingUrl, jobId, batchName }: ClusterGraphProps) {
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editValue, setEditValue] = useState('');
  const [exporting, setExporting] = useState(false);
  const [selectedClusters, setSelectedClusters] = useState<Set<number>>(new Set());
  const [extracting, setExtracting] = useState(false);

  // Sort clusters so biggest are first
  const sortedClusters = [...clusters].sort((a, b) => b.length - a.length);

  const startEditing = (idx: number) => {
    setEditingIdx(idx);
    setEditValue(labels[String(idx)] || '');
  };

  const commitLabel = (idx: number) => {
    onLabelChange(idx, editValue.trim());
    setEditingIdx(null);
  };

  const handleKeyDown = (e: React.KeyboardEvent, idx: number) => {
    if (e.key === 'Enter') commitLabel(idx);
    if (e.key === 'Escape') setEditingIdx(null);
  };

  const handleBulkExport = async () => {
    if (!jobId) return;
    setExporting(true);
    try {
      const response = await fetch(`http://localhost:8000/api/export-callertunes/${jobId}`, {
        method: 'POST'
      });
      if (response.ok) {
        const data = await response.json();
        alert(`Success! ${data.message}`);
      } else {
        const errData = await response.json().catch(() => ({}));
        alert(`Export failed: ${errData.detail || response.statusText}`);
      }
    } catch (e) {
      alert(`Export failed: ${e}`);
    } finally {
      setExporting(false);
    }
  };

  const handleExtract = async () => {
    if (!batchName) {
      alert("No batch name provided. Please save the batch first before extracting!");
      return;
    }
    if (selectedClusters.size === 0) return;

    setExtracting(true);
    const selectedData = Array.from(selectedClusters).map(idx => {
      const cluster = sortedClusters[idx];
      return {
        label: labels[String(idx)] || `Cluster ${idx + 1}`,
        urls: cluster
      };
    });

    try {
      const response = await fetch(`http://localhost:8000/api/extract-clusters`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch_name: batchName, selected_clusters: selectedData })
      });
      if (response.ok) {
        const data = await response.json();
        alert(`Success! ${data.message}`);
        setSelectedClusters(new Set());
      } else {
        const errData = await response.json().catch(() => ({}));
        alert(`Extraction failed: ${errData.detail || response.statusText}`);
      }
    } catch (e) {
      alert(`Extraction failed: ${e}`);
    } finally {
      setExtracting(false);
    }
  };

  const toggleSelect = (idx: number) => {
    const next = new Set(selectedClusters);
    if (next.has(idx)) next.delete(idx);
    else next.add(idx);
    setSelectedClusters(next);
  };

  return (
    <div className="w-full h-full flex flex-col">
      {/* Top action bar */}
      <div className="flex justify-between items-center px-4 py-3 border-b border-white/5 bg-black/10">
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-400">{selectedClusters.size} selected</span>
          {selectedClusters.size > 0 && (
            <button
              onClick={() => setSelectedClusters(new Set())}
              className="text-[10px] uppercase tracking-wider text-gray-500 hover:text-gray-300 font-bold"
            >
              Clear
            </button>
          )}
        </div>
        <button
          onClick={handleExtract}
          disabled={extracting || selectedClusters.size === 0}
          className="bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-300 text-xs font-bold py-1.5 px-4 rounded-lg transition-all disabled:opacity-30 flex items-center gap-2 border border-emerald-500/30"
        >
          {extracting ? 'Extracting...' : 'Extract Selected Clusters'}
        </button>
      </div>
      
      <div className="flex-1 p-4 overflow-y-auto grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
        {sortedClusters.map((cluster, i) => {
        const label = labels[String(i)] || '';
        const isEditing = editingIdx === i;
        const isCallerTunes = label.toLowerCase() === 'caller tunes';

        return (
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.3, delay: i * 0.03 }}
            key={i}
            className={`
              rounded-2xl p-5 transition-all flex flex-col group
              ${isCallerTunes 
                ? 'bg-indigo-900/30 border-2 border-indigo-500/60 shadow-[0_0_20px_rgba(99,102,241,0.15)] hover:bg-indigo-900/40' 
                : 'bg-white/[0.03] border border-white/10 hover:bg-white/[0.06]'
              }
            `}
          >
            {/* Header */}
            <div className="flex justify-between items-center mb-3">
              <div className="flex items-center gap-2">
                <input 
                  type="checkbox"
                  checked={selectedClusters.has(i)}
                  onChange={() => toggleSelect(i)}
                  className="w-4 h-4 rounded border-gray-600 text-cyan-500 focus:ring-cyan-500 focus:ring-offset-gray-900 bg-gray-700 cursor-pointer"
                />
                <h3 className="text-sm font-semibold text-gray-300">Cluster {i + 1}</h3>
              </div>
              <span className="text-xs bg-cyan-500/20 text-cyan-300 px-2 py-1 rounded-md font-medium">
                {cluster.length} clip{cluster.length > 1 ? 's' : ''}
              </span>
            </div>

            {/* Label section */}
            <div className="mb-4">
              {isEditing ? (
                <div className="flex items-center gap-2">
                  <input
                    autoFocus
                    value={editValue}
                    onChange={(e) => setEditValue(e.target.value)}
                    onKeyDown={(e) => handleKeyDown(e, i)}
                    onBlur={() => commitLabel(i)}
                    placeholder="e.g. Not Reachable — Hindi"
                    className="flex-1 bg-white/5 border border-cyan-500/40 rounded-lg px-3 py-1.5 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-cyan-400 transition-colors"
                  />
                  <button
                    onClick={() => commitLabel(i)}
                    className="p-1.5 rounded-lg bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30 transition-colors"
                  >
                    <Check size={14} />
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => startEditing(i)}
                  className={`
                    flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg transition-all w-full text-left
                    ${label
                      ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/25'
                      : 'bg-white/5 text-gray-500 border border-transparent hover:border-white/15 hover:text-gray-400'
                    }
                  `}
                >
                  <Tag size={12} className="flex-shrink-0" />
                  <span className="truncate">{label || 'Add label...'}</span>
                </button>
              )}
              
              {isCallerTunes && (
                <button
                  onClick={handleBulkExport}
                  disabled={exporting}
                  className="mt-2 text-xs bg-indigo-600/20 text-indigo-400 hover:bg-indigo-600/30 px-3 py-1.5 rounded transition-colors w-full flex items-center justify-center gap-2"
                  title="Exports all audio files to 'backend/exported_callertunes'"
                >
                  {exporting ? (
                    <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                  ) : (
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                    </svg>
                  )}
                  {exporting ? 'Exporting...' : 'Bulk Export All'}
                </button>
              )}
            </div>

            {/* Audio clip bubbles */}
            <div className="flex flex-wrap gap-2 mt-auto">
              {cluster.map((url, j) => {
                const isPlaying = playingUrl === url;
                return (
                  <motion.button
                    key={j}
                    whileHover={{ scale: 1.15 }}
                    whileTap={{ scale: 0.9 }}
                    onClick={() => onSelectAudio(url)}
                    title={url.split('/').pop()}
                    className={`
                      w-9 h-9 rounded-full flex items-center justify-center text-[10px] font-bold
                      border transition-all cursor-pointer relative
                      ${isPlaying
                        ? 'bg-gradient-to-tr from-emerald-400 to-cyan-300 shadow-[0_0_16px_rgba(52,211,153,0.5)] border-emerald-300/60 text-[#0b0c10]'
                        : 'bg-gradient-to-tr from-blue-500 to-cyan-400 shadow-[0_0_10px_rgba(34,211,238,0.3)] border-white/20 hover:border-white/50 text-white/80'
                      }
                    `}
                  >
                    {isPlaying ? <Play size={12} fill="currentColor" /> : j + 1}
                    {/* Playing ring animation */}
                    {isPlaying && (
                      <motion.span
                        className="absolute inset-0 rounded-full border-2 border-emerald-400/60"
                        animate={{ scale: [1, 1.4], opacity: [0.6, 0] }}
                        transition={{ duration: 1.2, repeat: Infinity, ease: 'easeOut' }}
                      />
                    )}
                  </motion.button>
                );
              })}
            </div>
          </motion.div>
        );
      })}
      </div>
    </div>
  );
}

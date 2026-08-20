import { useState } from 'react';
import { motion } from 'framer-motion';
import { Tag, Check, Play } from 'lucide-react';

interface ClusterGraphProps {
  clusters: string[][];
  labels: Record<string, string>;
  onSelectAudio: (url: string) => void;
  onLabelChange: (clusterIdx: number, label: string) => void;
  playingUrl: string | null;
}

export default function ClusterGraph({ clusters, labels, onSelectAudio, onLabelChange, playingUrl }: ClusterGraphProps) {
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editValue, setEditValue] = useState('');

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

  return (
    <div className="w-full h-full p-4 overflow-y-auto grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
      {sortedClusters.map((cluster, i) => {
        const label = labels[String(i)] || '';
        const isEditing = editingIdx === i;

        return (
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.3, delay: i * 0.03 }}
            key={i}
            className="bg-white/[0.03] border border-white/10 rounded-2xl p-5 hover:bg-white/[0.06] transition-colors flex flex-col group"
          >
            {/* Header */}
            <div className="flex justify-between items-center mb-3">
              <h3 className="text-sm font-semibold text-gray-300">Cluster {i + 1}</h3>
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
  );
}

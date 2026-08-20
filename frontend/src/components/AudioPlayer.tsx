import { useRef, useState, useEffect } from 'react';
import { Play, Pause, X, Copy, Check, Download } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

interface AudioPlayerProps {
  src: string;
  onClose: () => void;
}

export default function AudioPlayer({ src, onClose }: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [copied, setCopied] = useState(false);
  const [progress, setProgress] = useState(0);
  const [duration, setDuration] = useState(0);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    // Autoplay when src changes
    setProgress(0);
    setDuration(0);
    if (audioRef.current) {
      audioRef.current.play().catch(e => console.log('Autoplay blocked', e));
      setIsPlaying(true);
    }
  }, [src]);

  const togglePlay = () => {
    if (audioRef.current) {
      if (isPlaying) {
        audioRef.current.pause();
      } else {
        audioRef.current.play();
      }
      setIsPlaying(!isPlaying);
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(src);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownload = async () => {
    setDownloading(true);
    try {
      // Try the proxy endpoint first (cached file)
      const proxyUrl = `http://localhost:8000/api/audio-proxy?url=${encodeURIComponent(src)}`;
      const response = await fetch(proxyUrl);
      if (response.ok) {
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = src.split('/').pop() || 'audio.wav';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      } else {
        // Fallback: direct download
        const a = document.createElement('a');
        a.href = src;
        a.download = src.split('/').pop() || 'audio.wav';
        a.target = '_blank';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      }
    } catch {
      // Fallback: open in new tab
      window.open(src, '_blank');
    } finally {
      setDownloading(false);
    }
  };

  const handleTimeUpdate = () => {
    if (audioRef.current) {
      setProgress(audioRef.current.currentTime);
    }
  };

  const handleLoadedMetadata = () => {
    if (audioRef.current) {
      setDuration(audioRef.current.duration);
    }
  };

  const handleSeek = (e: React.MouseEvent<HTMLDivElement>) => {
    if (audioRef.current && duration > 0) {
      const rect = e.currentTarget.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const pct = x / rect.width;
      audioRef.current.currentTime = pct * duration;
    }
  };

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  };

  const progressPct = duration > 0 ? (progress / duration) * 100 : 0;

  return (
    <AnimatePresence>
      <motion.div 
        initial={{ y: 100, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 100, opacity: 0 }}
        className="fixed bottom-6 left-1/2 -translate-x-1/2 w-[95%] max-w-2xl glass rounded-2xl px-5 py-4 z-50 border border-white/20 shadow-[0_10px_40px_rgba(34,211,238,0.15)]"
      >
        {/* Progress seek bar */}
        <div
          className="w-full h-1.5 bg-white/10 rounded-full mb-3 cursor-pointer group"
          onClick={handleSeek}
        >
          <motion.div
            className="h-full bg-gradient-to-r from-cyan-400 to-blue-500 rounded-full relative"
            style={{ width: `${progressPct}%` }}
          >
            <span className="absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full shadow-md opacity-0 group-hover:opacity-100 transition-opacity" />
          </motion.div>
        </div>

        <div className="flex items-center gap-4">
          {/* Play / Pause */}
          <button 
            onClick={togglePlay}
            className="w-11 h-11 flex-shrink-0 rounded-full bg-cyan-500 hover:bg-cyan-400 text-black flex items-center justify-center transition-all hover:scale-105 active:scale-95"
          >
            {isPlaying ? <Pause size={20} fill="currentColor" /> : <Play size={20} fill="currentColor" className="ml-0.5" />}
          </button>

          {/* Track info */}
          <div className="flex-1 min-w-0 flex flex-col">
            <div className="text-[10px] text-gray-500 font-medium tracking-wider uppercase mb-0.5">Now Playing</div>
            <div className="text-sm font-semibold text-gray-200 truncate" title={src}>
              {src.split('/').pop()}
            </div>
            {duration > 0 && (
              <div className="text-[10px] text-gray-500 font-mono mt-0.5">
                {formatTime(progress)} / {formatTime(duration)}
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="flex items-center gap-1">
            {/* Download */}
            <button 
              onClick={handleDownload}
              disabled={downloading}
              title="Download audio"
              className="p-2.5 rounded-xl hover:bg-white/10 text-gray-400 hover:text-cyan-300 transition-colors disabled:opacity-50"
            >
              <Download size={18} className={downloading ? 'animate-bounce' : ''} />
            </button>

            {/* Copy URL */}
            <button 
              onClick={handleCopy}
              title="Copy URL"
              className="p-2.5 rounded-xl hover:bg-white/10 text-gray-400 hover:text-gray-200 transition-colors"
            >
              {copied ? <Check size={18} className="text-green-400" /> : <Copy size={18} />}
            </button>

            {/* Close */}
            <button 
              onClick={onClose}
              title="Close player"
              className="p-2.5 rounded-xl hover:bg-white/10 text-gray-400 hover:text-gray-200 transition-colors"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Hidden Audio Element */}
        <audio 
          ref={audioRef} 
          src={src} 
          onEnded={() => setIsPlaying(false)}
          onPause={() => setIsPlaying(false)}
          onPlay={() => setIsPlaying(true)}
          onTimeUpdate={handleTimeUpdate}
          onLoadedMetadata={handleLoadedMetadata}
          className="hidden" 
        />
      </motion.div>
    </AnimatePresence>
  );
}

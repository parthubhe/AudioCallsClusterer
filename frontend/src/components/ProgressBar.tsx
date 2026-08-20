import { motion } from 'framer-motion';

interface ProgressBarProps {
  phase: string;
  current: number;
  total: number;
  message: string;
}

const phaseColors: Record<string, string> = {
  starting: 'from-violet-500 to-blue-500',
  downloading: 'from-blue-500 to-cyan-400',
  extracting: 'from-cyan-400 to-teal-400',
  bucketing: 'from-teal-400 to-emerald-400',
  clustering: 'from-emerald-400 to-lime-400',
  done: 'from-lime-400 to-green-400',
};

const phaseIcons: Record<string, string> = {
  starting: '🚀',
  downloading: '⬇️',
  extracting: '🔍',
  bucketing: '📦',
  clustering: '🧬',
  done: '✅',
};

export default function ProgressBar({ phase, current, total, message }: ProgressBarProps) {
  const pct = total > 0 ? Math.round((current / total) * 100) : 0;
  const gradient = phaseColors[phase] || phaseColors.downloading;
  const icon = phaseIcons[phase] || '⏳';

  return (
    <div className="w-full">
      {/* Phase label + counter */}
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-gray-300 flex items-center gap-2">
          <span className="text-base">{icon}</span>
          {message}
        </span>
        {total > 1 && phase !== 'done' && (
          <span className="text-xs font-mono text-gray-500 bg-white/5 px-2 py-0.5 rounded-md">
            {current} / {total}
          </span>
        )}
      </div>

      {/* Bar track */}
      <div className="relative w-full h-3 bg-white/5 rounded-full overflow-hidden border border-white/10">
        {/* Animated fill */}
        <motion.div
          className={`absolute inset-y-0 left-0 rounded-full bg-gradient-to-r ${gradient}`}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.4, ease: 'easeOut' }}
        />
        {/* Glow pulse overlay */}
        {phase !== 'done' && (
          <motion.div
            className={`absolute inset-y-0 left-0 rounded-full bg-gradient-to-r ${gradient} opacity-40 blur-sm`}
            initial={{ width: 0 }}
            animate={{ width: `${pct}%` }}
            transition={{ duration: 0.4, ease: 'easeOut' }}
          />
        )}
      </div>

      {/* Percentage */}
      <div className="mt-1 text-right">
        <span className="text-xs font-mono text-gray-500">{pct}%</span>
      </div>
    </div>
  );
}

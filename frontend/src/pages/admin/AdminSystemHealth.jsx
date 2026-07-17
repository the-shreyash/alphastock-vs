import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Activity, Cpu, HardDrive, Database, Wifi, Server, RefreshCw } from "lucide-react";
import adminService from "../../services/adminService";

export default function AdminSystemHealth() {
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try { const { data } = await adminService.getSystemHealth(); setHealth(data); } catch { /* */ }
    setLoading(false);
  };
  useEffect(() => { load(); }, []);

  if (loading) return <div className="space-y-6"><div className="h-8 w-48 rounded-lg animate-pulse" style={{ background: "var(--border)" }} /><div className="grid grid-cols-3 gap-4">{Array.from({length:6}).map((_,i)=><div key={i} className="h-40 rounded-2xl animate-pulse" style={{background:"var(--bg-surface)"}}/>)}</div></div>;

  const sys = health?.system || {};
  const overall = health?.overall_status;
  const overallColor = overall === "healthy" ? "#00D68F" : overall === "degraded" ? "#FFB224" : "#FF6B6B";

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">System Health</h1>
          <p className="page-subtitle mt-1">Live infrastructure monitoring</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-xl" style={{ background: `${overallColor}15` }}>
            <div className="w-2.5 h-2.5 rounded-full" style={{ background: overallColor, boxShadow: `0 0 8px ${overallColor}80` }} />
            <span className="text-xs font-semibold capitalize" style={{ color: overallColor }}>{overall}</span>
          </div>
          <button onClick={() => { setLoading(true); load(); }} className="p-2 rounded-lg transition-all" style={{ color: "var(--text-muted)" }}
            onMouseEnter={e => { e.currentTarget.style.color = "var(--ai-accent)"; e.currentTarget.style.background = "var(--ai-accent-soft)"; }}
            onMouseLeave={e => { e.currentTarget.style.color = "var(--text-muted)"; e.currentTarget.style.background = "transparent"; }}>
            <RefreshCw size={16} />
          </button>
        </div>
      </div>

      {/* Resource Gauges */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <GaugeCard icon={Cpu} label="CPU" value={sys.cpu_percent || 0} unit="%" color="#6366F1" detail={`${sys.cpu_percent || 0}% utilized`} />
        <GaugeCard icon={Server} label="RAM" value={sys.ram_percent || 0} unit="%" color="#8B5CF6" detail={`${sys.ram_used_gb || 0} / ${sys.ram_total_gb || 0} GB`} />
        <GaugeCard icon={HardDrive} label="Disk" value={sys.disk_percent || 0} unit="%" color="#EC4899" detail={`${sys.disk_used_gb || 0} / ${sys.disk_total_gb || 0} GB`} />
      </div>

      {/* Service Status */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <ServiceCard name="MongoDB" status={health?.mongodb?.status} details={[
          { label: "Collections", value: health?.mongodb?.collections || 0 },
          { label: "Data Size", value: `${health?.mongodb?.data_size_mb || 0} MB` },
          { label: "Storage", value: `${health?.mongodb?.storage_size_mb || 0} MB` },
        ]} icon={Database} />
        <ServiceCard name="Redis" status={health?.redis?.status} details={[
          { label: "Status", value: health?.redis?.status || "—" },
        ]} icon={Database} />
        <ServiceCard name="WebSockets" status={health?.websockets?.status} details={[
          { label: "Active", value: health?.websockets?.active_connections || 0 },
        ]} icon={Wifi} />
        <ServiceCard name="Scheduler" status={health?.scheduler?.status} details={[
          { label: "Status", value: health?.scheduler?.status || "—" },
        ]} icon={Activity} />
      </div>

      {/* Platform Info */}
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="p-5 rounded-2xl" style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}>
        <h3 className="card-title mb-3">Platform</h3>
        <div className="grid grid-cols-3 gap-4">
          <div><span className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: "var(--text-muted)" }}>Python</span><div className="text-sm font-mono mt-0.5" style={{ color: "var(--text-primary)" }}>{health?.platform?.python_version}</div></div>
          <div><span className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: "var(--text-muted)" }}>OS</span><div className="text-sm font-mono mt-0.5" style={{ color: "var(--text-primary)" }}>{health?.platform?.os}</div></div>
          <div><span className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: "var(--text-muted)" }}>Architecture</span><div className="text-sm font-mono mt-0.5" style={{ color: "var(--text-primary)" }}>{health?.platform?.architecture}</div></div>
        </div>
      </motion.div>
    </div>
  );
}

function GaugeCard({ icon: Icon, label, value, unit, color, detail }) {
  const pct = Math.min(value, 100);
  const statusColor = pct > 90 ? "#FF6B6B" : pct > 70 ? "#FFB224" : "#00D68F";
  return (
    <motion.div initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} className="p-5 rounded-2xl" style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-3 mb-4">
        <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `${color}15` }}><Icon size={18} style={{ color }} /></div>
        <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{label}</span>
      </div>
      <div className="relative mb-3">
        <div className="h-3 rounded-full overflow-hidden" style={{ background: "var(--border)" }}>
          <motion.div initial={{ width: 0 }} animate={{ width: `${pct}%` }} transition={{ duration: 0.8, ease: "easeOut" }} className="h-full rounded-full" style={{ background: `linear-gradient(90deg, ${statusColor}, ${color})` }} />
        </div>
      </div>
      <div className="flex items-end justify-between">
        <div className="stat-value !text-2xl" style={{ color: statusColor }}>{value}<span className="text-sm font-normal ml-0.5">{unit}</span></div>
        <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>{detail}</span>
      </div>
    </motion.div>
  );
}

function ServiceCard({ name, status, details, icon: Icon }) {
  const color = status === "healthy" || status === "running" ? "#00D68F" : status === "not_configured" ? "#F59E0B" : "#FF6B6B";
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="p-5 rounded-2xl" style={{ background: "var(--bg-card-glass)", backdropFilter: "blur(24px)", border: "1px solid var(--border)" }}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Icon size={16} style={{ color: "var(--text-muted)" }} />
          <h4 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{name}</h4>
        </div>
        <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full" style={{ background: `${color}15` }}>
          <div className="w-1.5 h-1.5 rounded-full" style={{ background: color, boxShadow: `0 0 6px ${color}80` }} />
          <span className="text-[10px] font-semibold capitalize" style={{ color }}>{status?.replace("_", " ")}</span>
        </div>
      </div>
      <div className="space-y-1.5">
        {details.map(d => (
          <div key={d.label} className="flex items-center justify-between">
            <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>{d.label}</span>
            <span className="text-xs font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{d.value}</span>
          </div>
        ))}
      </div>
    </motion.div>
  );
}

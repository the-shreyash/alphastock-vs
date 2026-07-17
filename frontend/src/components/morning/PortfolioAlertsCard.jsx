import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { ShieldAlert, AlertOctagon, AlertTriangle, Info, Briefcase } from "lucide-react";
import SectionUnavailable from "./SectionUnavailable";

/**
 * PortfolioAlertsCard — the briefing's payoff: what this morning means for the
 * user's actual holdings.
 *
 * Everything else in the report is identical for every user. This section is
 * computed per request from live holdings (never cached into the shared report
 * document) and is what turns "Banking is down 1.4%" into "Banking is down and
 * you hold HDFCBANK".
 *
 * Every alert carries its reasoning — per the product rule that the AI educates
 * rather than asserts, the user always sees *why* they are being told something.
 */
const SEVERITY = {
  critical: { icon: AlertOctagon, color: "var(--loss)", bg: "rgba(244,63,94,0.10)", border: "rgba(244,63,94,0.25)" },
  warning: { icon: AlertTriangle, color: "#f59e0b", bg: "rgba(245,158,11,0.10)", border: "rgba(245,158,11,0.25)" },
  info: { icon: Info, color: "#818cf8", bg: "rgba(99,102,241,0.10)", border: "rgba(99,102,241,0.22)" },
};

function Alert({ alert }) {
  const style = SEVERITY[alert.severity] || SEVERITY.info;
  const Icon = style.icon;
  return (
    <div
      className="rounded-xl p-3.5"
      style={{ background: style.bg, border: `1px solid ${style.border}` }}
    >
      <div className="flex items-start gap-2.5">
        <Icon size={15} className="mt-0.5 shrink-0" style={{ color: style.color }} />
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>
              {alert.title}
            </p>
            {alert.symbol && (
              <Link
                to={`/stock/${alert.symbol}`}
                className="text-[10px] font-mono font-bold px-1.5 py-0.5 rounded transition-opacity hover:opacity-70"
                style={{ background: "var(--bg-surface)", color: "var(--text-secondary)" }}
              >
                {alert.symbol}
              </Link>
            )}
          </div>
          <p className="text-[13px] mt-1 leading-relaxed" style={{ color: "var(--text-secondary)" }}>
            {alert.message}
          </p>
          {alert.why && (
            <p className="text-[12px] mt-1.5 leading-relaxed" style={{ color: "var(--text-muted)" }}>
              <span className="font-semibold">Why: </span>{alert.why}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export default function PortfolioAlertsCard({ portfolio }) {
  const alerts = portfolio?.alerts || [];
  const risk = portfolio?.risk;

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.4 }}
    >
      <div className="flex items-center justify-between mb-3">
        <h3 className="eyebrow flex items-center gap-2">
          <ShieldAlert size={13} /> Your Portfolio This Morning
        </h3>
        {risk?.score !== undefined && (
          <span className="text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>
            Risk{" "}
            <span
              className="font-semibold"
              style={{
                color: risk.level === "High" ? "var(--loss)"
                  : risk.level === "Elevated" ? "#f59e0b" : "var(--gain)",
              }}
            >
              {risk.level} {risk.score}/100
            </span>
          </span>
        )}
      </div>

      {portfolio?.available === false ? (
        <SectionUnavailable note={portfolio?.note} icon={Briefcase} />
      ) : alerts.length === 0 ? (
        <div className="glass-card p-5 text-center">
          <Briefcase size={22} className="mx-auto mb-2 opacity-40" style={{ color: "var(--text-muted)" }} />
          <p className="text-[13px]" style={{ color: "var(--text-muted)" }}>
            {portfolio?.note || "Nothing needs your attention this morning — no alerts on your holdings."}
          </p>
          {!portfolio?.holdings_count && (
            <Link
              to="/portfolio"
              className="inline-block mt-3 text-[12px] font-semibold hover:underline"
              style={{ color: "var(--ai-accent)" }}
            >
              Go to Portfolio →
            </Link>
          )}
        </div>
      ) : (
        <div className="space-y-2.5">
          {alerts.map((a, i) => <Alert key={i} alert={a} />)}
        </div>
      )}
    </motion.div>
  );
}

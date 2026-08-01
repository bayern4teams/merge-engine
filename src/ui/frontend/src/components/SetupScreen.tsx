import { useEffect, useState } from "react";
import {
  addRecent,
  getRecent,
  RecentItem,
  OriginConfig,
  DestinationConfig,
} from "../api";
import ConnectionForm, { ConnValues } from "./ConnectionForm";

interface Props {
  origins: OriginConfig[];
  destination: DestinationConfig | null;
  onOriginsChange: (o: OriginConfig[]) => void;
  onDestinationChange: (d: DestinationConfig | null) => void;
}

export default function SetupScreen({
  origins,
  destination,
  onOriginsChange,
  onDestinationChange,
}: Props) {
  const [recentOrigins, setRecentOrigins] = useState<RecentItem[]>([]);
  const [recentDests, setRecentDests] = useState<RecentItem[]>([]);

  useEffect(() => {
    getRecent()
      .then((r) => {
        setRecentOrigins(r.recent_origins);
        setRecentDests(r.recent_destinations);
      })
      .catch(() => {});
  }, []);

  function addOrigin(v: ConnValues) {
    const origin: OriginConfig = {
      id: `origin_${origins.length + 1}`,
      type: v.type,
      connection: {
        host: v.host,
        port: Number(v.port) || 5432,
        user: v.user,
        password: v.password,
        database: v.database,
      },
      priority: 0,
    };
    onOriginsChange([...origins, origin]);
    addRecent("origin", {
      type: v.type,
      connection: { ...origin.connection },
      priority: 0,
    })
      .then((r) => setRecentOrigins(r.items))
      .catch(() => {});
  }

  function removeOrigin(id: string) {
    onOriginsChange(origins.filter((o) => o.id !== id));
  }

  function setDestination(v: ConnValues) {
    const isCH = v.type === "clickhouse";
    const dest: DestinationConfig = {
      type: isCH ? "clickhouse" : "postgresql",
      host: v.host,
      port: Number(v.port) || (isCH ? 8123 : 5432),
      user: v.user,
      password: v.password,
      database: v.database,
      ...(isCH ? {} : { schema: v.schema || "public" }),
    };
    onDestinationChange(dest);
    addRecent("destination", {
      type: v.type,
      connection: {
        host: dest.host,
        port: dest.port,
        user: dest.user,
        password: dest.password,
        database: dest.database,
        ...(isCH ? {} : { schema: dest.schema }),
      },
    })
      .then((r) => setRecentDests(r.items))
      .catch(() => {});
  }

  return (
    <div>
      <ConnectionForm
        title="Origin databases"
        hint="Add one or more source databases to merge from. Fill the fields (discovery runs automatically on blur) and click Add origin."
        showSchema={false}
        recent={recentOrigins}
        submitLabel="＋ Add origin"
        onSubmit={(v) => addOrigin(v)}
      />

      {origins.length > 0 && (
        <div className="card">
          <div className="section-label">Configured origins ({origins.length})</div>
          <div className="config-list">
            {origins.map((o) => (
              <div key={o.id} className="config-item">
                <div>
                  <strong>{o.id}</strong>{" "}
                  <span className="meta">
                    {o.type} → {o.connection.host}:{o.connection.port}/
                    {o.connection.database}
                  </span>
                </div>
                <button className="btn btn-danger" onClick={() => removeOrigin(o.id)}>
                  Remove
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {destination ? (
        <div className="card">
          <h3 className="card-title">Destination database</h3>
          <div className="config-item" style={{ marginTop: 12 }}>
            <div>
              <span className="meta">
                {destination.type} → {destination.host}:{destination.port}/
                {destination.database}
                {destination.schema ? ` (${destination.schema})` : ""}
              </span>
            </div>
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <span className="locked-pill">✓ Locked</span>
              <button className="btn btn-ghost" onClick={() => onDestinationChange(null)}>
                🔄 Change
              </button>
            </div>
          </div>
          <p className="card-hint" style={{ marginTop: 12, marginBottom: 0 }}>
            Only one destination is allowed. Click Change to replace it.
          </p>
        </div>
      ) : (
        <ConnectionForm
          title="Destination database"
          hint="The single target database everything merges into. Exactly one destination is allowed."
          showSchema={true}
          recent={recentDests}
          submitLabel="Confirm destination"
          onSubmit={(v) => setDestination(v)}
        />
      )}
    </div>
  );
}

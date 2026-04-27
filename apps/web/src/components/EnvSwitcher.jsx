const ENVS = ["stage", "ppe", "prod"];

export default function EnvSwitcher({ env, onChange }) {
  return (
    <div className="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-surface-2 border border-outline-soft">
      <span className="label-caps">env</span>
      <select
        value={env}
        onChange={(e) => onChange(e.target.value)}
        className="bg-transparent text-sm text-primary font-tech focus:outline-none cursor-pointer"
      >
        {ENVS.map((e) => <option key={e} value={e}>{e}</option>)}
      </select>
    </div>
  );
}

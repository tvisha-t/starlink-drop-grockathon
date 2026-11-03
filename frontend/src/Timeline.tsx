/**
 * The timeline: an outage curve (people without service over the hours after
 * the flood starts) that doubles as the slider. Dragging it moves the whole map.
 */
type Props = {
  hours: number[];
  peopleOut: number[];
  hourIdx: number;
  onChange: (idx: number) => void;
};

const W = 640;
const H = 74;
const PAD_X = 18;

export default function Timeline({ hours, peopleOut, hourIdx, onChange }: Props) {
  const max = Math.max(...peopleOut, 1);
  const x = (i: number) => PAD_X + (i / (hours.length - 1)) * (W - 2 * PAD_X);
  const y = (v: number) => H - 22 - (v / max) * (H - 34);
  const line = peopleOut.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
  const area = `${line} L${x(hours.length - 1)},${H - 22} L${x(0)},${H - 22} Z`;

  return (
    <div className="timeline">
      <div className="timeline-readout">
        <span className="timeline-big">{Math.round(peopleOut[hourIdx]).toLocaleString()}</span>
        <span className="timeline-small">
          people without cell service
          <br />
          {hours[hourIdx] === 0 ? "as the rain starts" : `${hours[hourIdx]} hours in`}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="timeline-svg" role="img" aria-label="People without service by hour">
        <path d={area} className="timeline-area" />
        <path d={line} className="timeline-line" />
        {hours.map((h, i) => (
          <g key={h}>
            <line x1={x(i)} x2={x(i)} y1={H - 22} y2={H - 18} className="timeline-tick" />
            <text x={x(i)} y={H - 6} textAnchor="middle" className={i === hourIdx ? "timeline-label on" : "timeline-label"}>
              {h}h
            </text>
          </g>
        ))}
        <line x1={x(hourIdx)} x2={x(hourIdx)} y1={4} y2={H - 22} className="timeline-cursor" />
        <circle cx={x(hourIdx)} cy={y(peopleOut[hourIdx])} r={5} className="timeline-dot" />
      </svg>
      <input
        type="range"
        className="timeline-range"
        min={0}
        max={hours.length - 1}
        step={1}
        value={hourIdx}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label="Hours since the flood started"
      />
    </div>
  );
}

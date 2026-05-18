import { useMemo } from 'react';

export interface Goal {
  id: string;
  title: string;
  assigned?: string | null;
  done: boolean;
  model?: 'sonnet' | 'opus' | null;
}

interface TreeNode {
  goal: Goal;
  synthetic: boolean;
  children: TreeNode[];
}

// Matches G-NNN or G-NNN-suffix; captures numeric root and optional suffix
const GOAL_RE = /^(G-\d+)(-(.+))?$/;

function getParentId(id: string, ids: Set<string>): string | null {
  const m = id.match(GOAL_RE);
  if (!m || !m[2]) return null; // root node
  const root = m[1];
  const parts = m[3].split('-');
  // Try longest ancestor suffix first (e.g. G-001-a for G-001-a-1)
  for (let i = parts.length - 1; i >= 1; i--) {
    const candidate = `${root}-${parts.slice(0, i).join('-')}`;
    if (ids.has(candidate)) return candidate;
  }
  return root;
}

function buildTree(goals: Goal[]): TreeNode[] {
  const goalMap = new Map<string, Goal>(goals.map(g => [g.id, g]));

  // Iteratively add synthetic placeholders until all parent references are satisfied
  let changed = true;
  while (changed) {
    changed = false;
    for (const g of [...goalMap.values()]) {
      const pid = getParentId(g.id, new Set(goalMap.keys()));
      if (pid && !goalMap.has(pid)) {
        goalMap.set(pid, { id: pid, title: pid, done: false });
        changed = true;
      }
    }
  }

  const originalIds = new Set(goals.map(g => g.id));
  const syntheticIds = new Set([...goalMap.keys()].filter(id => !originalIds.has(id)));
  const allIds = new Set(goalMap.keys());

  const childrenMap = new Map<string, string[]>();
  const roots: string[] = [];

  for (const id of goalMap.keys()) {
    const pid = getParentId(id, allIds);
    if (!pid) {
      roots.push(id);
    } else {
      if (!childrenMap.has(pid)) childrenMap.set(pid, []);
      childrenMap.get(pid)!.push(id);
    }
  }

  function toNode(id: string): TreeNode {
    const goal = goalMap.get(id)!;
    const children = (childrenMap.get(id) ?? [])
      .sort((a, b) => a.localeCompare(b))
      .map(toNode);
    return { goal, synthetic: syntheticIds.has(id), children };
  }

  return roots.sort((a, b) => a.localeCompare(b)).map(toNode);
}

const s = {
  container: {
    fontFamily: 'sans-serif',
    fontSize: '13px',
    color: '#e0e0e0',
    background: '#1a1a2e',
    padding: '16px',
    borderRadius: '8px',
  } as React.CSSProperties,
  ul: { listStyle: 'none', margin: 0, padding: 0 } as React.CSSProperties,
  li: (depth: number): React.CSSProperties => ({
    paddingLeft: `${depth * 20}px`,
    marginBottom: '6px',
  }),
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    flexWrap: 'wrap',
    padding: '4px 0',
  } as React.CSSProperties,
  goalId: {
    fontFamily: 'monospace',
    color: '#a0c4ff',
    fontWeight: 600,
    fontSize: '12px',
    flexShrink: 0,
  } as React.CSSProperties,
  title: (synthetic: boolean): React.CSSProperties => ({
    color: synthetic ? '#555577' : '#d0d0e0',
    fontStyle: synthetic ? 'italic' : 'normal',
  }),
  assigned: {
    color: '#7a7a9a',
    fontSize: '11px',
    flexShrink: 0,
  } as React.CSSProperties,
  badge: (color: string, bg: string): React.CSSProperties => ({
    fontSize: '10px',
    fontWeight: 700,
    padding: '1px 7px',
    borderRadius: '10px',
    color,
    background: bg,
    letterSpacing: '0.03em',
    flexShrink: 0,
  }),
  empty: {
    color: '#4a4a6a',
    padding: '24px 0',
    fontStyle: 'italic',
  } as React.CSSProperties,
};

function StatusBadge({ goal }: { goal: Goal }) {
  if (goal.done)
    return <span style={s.badge('#fff', '#2b8a3e')}>✓ done</span>;
  if (goal.assigned)
    return <span style={s.badge('#fff', '#1971c2')}>assigned</span>;
  return <span style={s.badge('#999', '#2a2a40')}>pending</span>;
}

function ModelBadge({ model }: { model: Goal['model'] }) {
  if (!model) return null;
  const cfg =
    model === 'sonnet'
      ? { color: '#fff', bg: '#2b8a3e' }
      : { color: '#fff', bg: '#d9480f' };
  return <span style={s.badge(cfg.color, cfg.bg)}>{model}</span>;
}

function NodeRow({ node, depth }: { node: TreeNode; depth: number }) {
  const { goal, synthetic } = node;
  return (
    <li style={s.li(depth)}>
      <div style={s.row}>
        <span style={s.goalId}>{goal.id}</span>
        <span style={s.title(synthetic)}>{goal.title}</span>
        {goal.assigned && (
          <span style={s.assigned}>@{goal.assigned}</span>
        )}
        <StatusBadge goal={goal} />
        <ModelBadge model={goal.model} />
      </div>
      {node.children.length > 0 && (
        <ul style={s.ul}>
          {node.children.map(child => (
            <NodeRow key={child.goal.id} node={child} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

export interface PlanTreeProps {
  goals: Goal[];
}

export function PlanTree({ goals }: PlanTreeProps) {
  const tree = useMemo(() => buildTree(goals), [goals]);

  if (tree.length === 0) {
    return <div style={s.container}><span style={s.empty}>no goals</span></div>;
  }

  return (
    <div style={s.container}>
      <ul style={s.ul}>
        {tree.map(node => (
          <NodeRow key={node.goal.id} node={node} depth={0} />
        ))}
      </ul>
    </div>
  );
}

export default PlanTree;

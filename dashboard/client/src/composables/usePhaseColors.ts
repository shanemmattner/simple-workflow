const phaseColorMap: Record<string, string> = {
  triage: '#3B82F6',
  plan: '#8B5CF6',
  'test-plan': '#A855F7',
  'wave-planner': '#6366F1',
  execute: '#22C55E',
  review: '#F59E0B',
};

export function usePhaseColors() {
  function hashString(str: string): number {
    let hash = 7151;
    for (let i = 0; i < str.length; i++) {
      hash = ((hash << 5) + hash) + str.charCodeAt(i);
    }
    return Math.abs(hash >>> 0);
  }

  const fallbackColors = [
    '#3B82F6', '#22C55E', '#EAB308', '#A855F7',
    '#EC4899', '#6366F1', '#EF4444', '#F97316',
    '#14B8A6', '#06B6D4',
  ];

  function getPhaseColor(phaseName: string): string {
    // Check known prefixes
    for (const [prefix, color] of Object.entries(phaseColorMap)) {
      if (phaseName.startsWith(prefix)) return color;
    }
    return fallbackColors[hashString(phaseName) % fallbackColors.length];
  }

  return { getPhaseColor };
}

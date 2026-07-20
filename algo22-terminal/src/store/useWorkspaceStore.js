import { create } from 'zustand';
import { persist } from 'zustand/middleware';

const DEFAULT_LAYOUT = [
  { i: 'leftPanel', x: 0, y: 0, w: 2, h: 8 },
  { i: 'centerPanel', x: 2, y: 0, w: 6, h: 5 },
  { i: 'rightPanel', x: 8, y: 0, w: 4, h: 5 },
  { i: 'bottomPanel', x: 2, y: 5, w: 10, h: 3 },
];

const useWorkspaceStore = create(
  persist(
    (set, get) => ({
      layout: DEFAULT_LAYOUT,
      theme: 'terminal-dark',
      
      // Update specific layout
      setLayout: (newLayout) => set({ layout: newLayout }),
      
      // Phase 2.5: Restore Default Layout
      restoreDefaultLayout: () => set({ layout: DEFAULT_LAYOUT }),
      
      // Phase 2.5: Export Layout JSON
      exportLayoutJSON: () => {
        const state = get();
        const json = JSON.stringify({ layout: state.layout, theme: state.theme }, null, 2);
        const blob = new Blob([json], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `aerora-workspace-${new Date().toISOString().slice(0,10)}.json`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
      },

      // Phase 2.5: Import Layout JSON
      importLayoutJSON: (jsonString) => {
        try {
          const parsed = JSON.parse(jsonString);
          if (parsed.layout && Array.isArray(parsed.layout)) {
            set({ layout: parsed.layout, theme: parsed.theme || 'terminal-dark' });
            return true;
          }
          return false;
        } catch (error) {
          console.error("Failed to parse workspace JSON", error);
          return false;
        }
      },
      
      // Phase 2.5: Hard Reset (clears all)
      resetWorkspace: () => {
        localStorage.removeItem('aerora-workspace-storage');
        set({ layout: DEFAULT_LAYOUT, theme: 'terminal-dark' });
      }
    }),
    {
      name: 'aerora-workspace-storage', // unique name for localStorage persistence
    }
  )
);

export default useWorkspaceStore;

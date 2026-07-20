import { create } from 'zustand';

export const useRiskStore = create((set) => ({
  portfolioExposure: { btc: 0, eth: 0, usdt: 100 },
  exchangeExposure: { binance: 0, okx: 0, bybit: 0 },
  marginUtilization: 0,
  drawdown: 0,
  killSwitchActive: false,
  circuitBreakers: {
    latencySpike: false,
    exchangeDisconnect: false,
    drawdownBreach: false
  },

  updatePortfolioExposure: (newExposure) => set({ portfolioExposure: newExposure }),
  updateExchangeExposure: (newExposure) => set({ exchangeExposure: newExposure }),
  updateMargin: (margin) => set({ marginUtilization: margin }),
  updateDrawdown: (dd) => set({ drawdown: dd }),
  
  toggleKillSwitch: () => set((state) => ({ killSwitchActive: !state.killSwitchActive })),
  
  tripCircuitBreaker: (breakerId) => set((state) => ({
    circuitBreakers: { ...state.circuitBreakers, [breakerId]: true }
  }))
}));

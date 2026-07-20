import { create } from 'zustand';
import { CircularBuffer } from '../utils/CircularBuffer';

const MAX_EVENTS = 1000;

export const useExecutionStore = create((set, get) => ({
  // Bounded queues using CircularBuffer
  orders: new CircularBuffer(MAX_EVENTS),
  fills: new CircularBuffer(MAX_EVENTS),
  signals: new CircularBuffer(MAX_EVENTS),
  logs: new CircularBuffer(MAX_EVENTS),

  // Actions called directly by websocketClient
  addOrder: (order) => {
    get().orders.push(order);
    set({ orders: get().orders }); // trigger render for components selecting 'orders'
  },
  
  addFill: (fill) => {
    get().fills.push(fill);
    set({ fills: get().fills });
  },
  
  addSignal: (signal) => {
    get().signals.push(signal);
    set({ signals: get().signals });
  },

  addLog: (log) => {
    get().logs.push(log);
    set({ logs: get().logs });
  },

  clearAll: () => {
    get().orders.clear();
    get().fills.clear();
    get().signals.clear();
    get().logs.clear();
    set({
      orders: get().orders,
      fills: get().fills,
      signals: get().signals,
      logs: get().logs
    });
  }
}));

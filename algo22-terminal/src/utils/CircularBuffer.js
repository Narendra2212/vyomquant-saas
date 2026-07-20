export class CircularBuffer {
  constructor(maxSize = 1000) {
    this.maxSize = maxSize;
    this.buffer = [];
  }

  push(item) {
    if (this.buffer.length >= this.maxSize) {
      this.buffer.shift(); // Remove oldest
    }
    this.buffer.push(item);
  }

  pushMultiple(items) {
    for (const item of items) {
      this.push(item);
    }
  }

  toArray() {
    return [...this.buffer];
  }

  clear() {
    this.buffer = [];
  }
}

import { useEffect, useState } from 'react'

export function useCountUp(end, duration = 1200, start = 0, isInView = false) {
  const [count, setCount] = useState(start)
  useEffect(() => {
    if (!isInView) return
    let startTime = null
    let animationFrame = null
    const animate = (timestamp) => {
      if (!startTime) startTime = timestamp
      const progress = Math.min((timestamp - startTime) / duration, 1)
      const eased = 1 - Math.pow(1 - progress, 3)
      setCount(Math.floor(eased * (end - start) + start))
      if (progress < 1) animationFrame = requestAnimationFrame(animate)
    }
    animationFrame = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(animationFrame)
  }, [end, duration, start, isInView])
  return count
}

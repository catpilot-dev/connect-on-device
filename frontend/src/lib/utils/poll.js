/**
 * Creates a reusable poll timer with start/stop lifecycle.
 * @param {Function} callback - async function to call on each tick
 * @param {number} interval - poll interval in ms (default 2000)
 */
export function createPoll(callback, interval = 2000) {
  let timer = null
  return {
    start() { if (!timer) timer = setInterval(callback, interval) },
    stop() { if (timer) { clearInterval(timer); timer = null } },
    get active() { return timer !== null },
  }
}

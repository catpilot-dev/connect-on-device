import { writable } from 'svelte/store'

/** The dongle_id of the active device */
export const dongleId = writable(null)

/** The currently selected route fullname (null = show list) */
export const selectedRoute = writable(null)

/** Whether the device uses metric units (from openpilot IsMetric param) */
export const isMetric = writable(true)

/** Storage info from /v1/storage ({total, free, used, percent_free}) */
export const storageInfo = writable(null)

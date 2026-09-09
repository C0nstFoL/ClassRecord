import { useEffect, useState } from 'react'
import { api, type Preset } from '../api'

/** 获取后端配置的课程热词预设列表，供录制页下拉框使用。 */
export function usePresets() {
  const [presets, setPresets] = useState<Preset[]>([])

  useEffect(() => {
    api
      .listPresets()
      .then(setPresets)
      .catch(() => setPresets([]))
  }, [])

  return presets
}

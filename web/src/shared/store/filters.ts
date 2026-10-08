import { create } from 'zustand'

export interface Filters {
  gov_level: string | null
  region: string | null
  category: string | null
  /** 一级分类下的行业门类（产业规划/农业农村…），与仪表盘「行业分布」同一套口径 */
  category_l2: string | null
  target_group: string | null
  target_industry: string | null
  keyword: string | null
}

interface FilterState extends Filters {
  set: (partial: Partial<Filters>) => void
  reset: () => void
}

const empty: Filters = {
  gov_level: null,
  region: null,
  category: null,
  category_l2: null,
  target_group: null,
  target_industry: null,
  keyword: null,
}

export const useFilters = create<FilterState>((set) => ({
  ...empty,
  set: (partial) => set(partial),
  reset: () => set(empty),
}))

import type { App } from 'vue'
import MarketSelector from './Global/MarketSelector.vue'

export function setupGlobalComponents(app: App) {
  app.component('MarketSelector', MarketSelector)
}

export default setupGlobalComponents

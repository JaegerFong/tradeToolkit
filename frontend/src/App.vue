<template>
  <div id="app" class="app-container">
    <router-view v-slot="{ Component, route }">
      <transition
        :name="(route?.meta?.transition as string) || 'fade'"
        mode="out-in"
        appear
      >
        <keep-alive :include="keepAliveComponents">
          <component :is="Component" :key="route?.fullPath || 'default'" />
        </keep-alive>
      </transition>
    </router-view>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const keepAliveComponents = computed(() => [
  'Dashboard',
])
</script>

<style lang="scss">
.app-container {
  min-height: 100vh;
  background-color: var(--el-bg-color-page);
  transition: background-color 0.3s ease;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.slide-left-enter-active,
.slide-left-leave-active {
  transition: all 0.3s ease;
}

.slide-left-enter-from {
  transform: translateX(30px);
  opacity: 0;
}

.slide-left-leave-to {
  transform: translateX(-30px);
  opacity: 0;
}

.slide-up-enter-active,
.slide-up-leave-active {
  transition: all 0.3s ease;
}

.slide-up-enter-from {
  transform: translateY(30px);
  opacity: 0;
}

.slide-up-leave-to {
  transform: translateY(-30px);
  opacity: 0;
}

@media (max-width: 768px) {
  .app-container {
    padding: 0;
  }
}
</style>

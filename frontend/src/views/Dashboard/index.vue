<template>
  <div class="dashboard">
    <div class="welcome-section">
      <div class="welcome-content">
        <h1 class="welcome-title">
          欢迎使用 tradeToolkit
          <span class="version-badge">v1.0.1</span>
        </h1>
        <p class="welcome-subtitle">
          数据采集 · 任务调度 · 规则引擎平台
        </p>
      </div>
      <div class="welcome-actions">
        <el-button type="primary" size="large" @click="goToStrategies">
          <el-icon><DataAnalysis /></el-icon>
          策略工具
        </el-button>
        <el-button size="large" @click="goToPatternScreening">
          <el-icon><Search /></el-icon>
          形态筛选
        </el-button>
      </div>
    </div>

    <el-row :gutter="20" class="quick-links">
      <el-col :span="8">
        <el-card shadow="hover" class="link-card" @click="goToScheduler">
          <el-icon size="32"><Clock /></el-icon>
          <h3>定时任务</h3>
          <p>管理和监控所有定时数据同步任务</p>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="hover" class="link-card" @click="goToSync">
          <el-icon size="32"><Connection /></el-icon>
          <h3>数据源同步</h3>
          <p>多数据源管理、优先级配置、数据同步</p>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="hover" class="link-card" @click="goToConfig">
          <el-icon size="32"><Setting /></el-icon>
          <h3>系统配置</h3>
          <p>API密钥、模型配置、缓存管理</p>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { Clock, Connection, DataAnalysis, Search, Setting } from '@element-plus/icons-vue'

const router = useRouter()

const goToStrategies = () => router.push('/strategies')
const goToPatternScreening = () => router.push('/pattern-screening')
const goToScheduler = () => router.push('/settings/scheduler')
const goToSync = () => router.push('/settings/sync')
const goToConfig = () => router.push('/settings/config')
</script>

<style scoped>
.dashboard {
  padding: 20px;
}
.welcome-section {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 12px;
  padding: 40px;
  margin-bottom: 24px;
  color: white;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.welcome-title {
  font-size: 28px;
  margin: 0 0 8px 0;
}
.version-badge {
  font-size: 12px;
  background: rgba(255,255,255,0.2);
  padding: 2px 8px;
  border-radius: 4px;
  vertical-align: middle;
}
.welcome-subtitle {
  font-size: 14px;
  opacity: 0.9;
  margin: 0;
}
.welcome-actions {
  display: flex;
  gap: 12px;
  flex-shrink: 0;
}
.quick-links {
  margin-top: 24px;
}
.link-card {
  cursor: pointer;
  text-align: center;
  padding: 20px;
  transition: transform 0.2s;
}
.link-card:hover {
  transform: translateY(-4px);
}
.link-card h3 {
  margin: 12px 0 8px;
  font-size: 16px;
}
.link-card p {
  color: #909399;
  font-size: 13px;
  margin: 0;
}
</style>

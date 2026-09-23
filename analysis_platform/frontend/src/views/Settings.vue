<!--
  对应文档: 四.V4.0 - 系统设置页面
  功能: 查看和修改系统配置
-->
<template>
  <div class="page-container">
    <h2 class="page-title">系统设置</h2>

    <el-card class="page-card">
      <template #header>萤石平台配置</template>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="AppKey">719aae30********</el-descriptions-item>
        <el-descriptions-item label="Token状态">
          <el-tag type="success">需调用API验证</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="DeepSeek模型">deepseek-v3</el-descriptions-item>
        <el-descriptions-item label="直播默认协议">HLS (2)</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card class="page-card">
      <template #header>设备激活码</template>
      <el-table :data="codes" stripe size="small">
        <el-table-column type="index" label="#" width="40" />
        <el-table-column prop="code" label="激活码" />
        <el-table-column width="200">
          <template #default="{ $index }">
            <template v-if="$index === 0">
              <el-tag type="success" size="small">已使用</el-tag>
              <span style="margin-left:8px;font-size:12px;color:#909399">CHANGE_ME_DEVICE_SERIAL</span>
            </template>
            <el-tag v-else type="info" size="small">未使用</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="page-card">
      <template #header>关于系统</template>
      <p>萤石平台接入分析平台 v1.0.0</p>
      <p style="color:#909399;font-size:13px;margin-top:8px">
        基于Vue3 + Flask构建，集成萤石(EZVIZ)开放平台API，<br>
        旨在为老年人跌倒检测系统提供统一的设备管理与监控界面。
      </p>
    </el-card>
  </div>
</template>

<script setup>
// [开发文档 四.V4.0 - 系统设置]
const codes = [
  { code: '249d2276-...' },
  { code: '9b70f05e-...' },
  { code: '18de0cfd-...' },
  { code: '2cab5913-...' },
  { code: '81b5ab50-...' }
]
</script>

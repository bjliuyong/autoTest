import request from '@/utils/request'

// 获取测试大盘统计指标
export function getDashboardStats() {
  return request({
    url: '/autotest/dashboard/stats',
    method: 'get'
  })
}

// 启动测试
export function startTest(data) {
  return request({
    url: '/autotest/dashboard/start',
    method: 'post',
    data: data
  })
}

// 停止测试
export function stopTest() {
  return request({
    url: '/autotest/dashboard/stop',
    method: 'post'
  })
}

// 触发自测模拟
export function runSimulation(count) {
  return request({
    url: '/autotest/dashboard/simulate?count=' + (count || 5),
    method: 'post'
  })
}

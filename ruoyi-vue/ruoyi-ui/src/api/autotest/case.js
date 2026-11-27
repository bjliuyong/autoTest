import request from '@/utils/request'

// 查询测试用例追溯列表
export function listCase(query) {
  return request({
    url: '/autotest/case/list',
    method: 'get',
    params: query
  })
}

// 查询测试用例详细追溯记录（含照片与日志切片）
export function getCase(caseRecordId) {
  return request({
    url: '/autotest/case/' + caseRecordId,
    method: 'get'
  })
}

// 删除用例记录
export function delCase(caseRecordId) {
  return request({
    url: '/autotest/case/' + caseRecordId,
    method: 'delete'
  })
}

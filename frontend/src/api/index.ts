export { ApiError } from './client'
import { getBootstrap } from './bootstrap'
import { getDoctor } from './doctor'
import { createJobFromNetlist, getJob, getJobs } from './jobs'
import { validateNetlist } from './netlists'
import { searchSymbols } from './symbols'
import {
  addWizardMessage,
  approveWizardSpec,
  clearWizardIr,
  createWizardSession,
  generateWizardIr,
  generateWizardProject,
  getWizardSession,
} from './wizard'

export const api = {
  getBootstrap,
  getDoctor,
  getJobs,
  getJob,
  searchSymbols,
  validateNetlist,
  createJobFromNetlist,
  createWizardSession,
  getWizardSession,
  addWizardMessage,
  approveWizardSpec,
  clearWizardIr,
  generateWizardIr,
  generateWizardProject,
}

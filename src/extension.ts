import * as vscode from 'vscode';
import { registerContextBlame } from './commands/contextBlame';
import { registerTimelineSummary } from './commands/timelineSummary';
import { registerRequirementTrace } from './commands/requirementTrace';

export function activate(context: vscode.ExtensionContext) {
    registerContextBlame(context);
    registerTimelineSummary(context);
    registerRequirementTrace(context);
}

export function deactivate() {}

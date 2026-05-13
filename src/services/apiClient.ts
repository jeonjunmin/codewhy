import * as vscode from 'vscode';
import axios, { AxiosInstance } from 'axios';

function createClient(): AxiosInstance {
    const config = vscode.workspace.getConfiguration('codewhy');
    const baseURL = config.get<string>('backendUrl', 'http://localhost:8000');
    return axios.create({ baseURL, timeout: 30000 });
}

export async function postContextBlame(payload: {
    filePath: string;
    line: number;
    repoPath: string;
}): Promise<{ explanation: string; commitHash: string; author: string; date: string }> {
    const client = createClient();
    const { data } = await client.post('/api/blame/context', payload);
    return data;
}

export async function postTimelineSummary(payload: {
    filePath: string;
    repoPath: string;
}): Promise<{ summary: string; milestones: { date: string; description: string }[] }> {
    const client = createClient();
    const { data } = await client.post('/api/timeline/summary', payload);
    return data;
}

export async function postRequirementTrace(payload: {
    filePath: string;
    line: number;
    repoPath: string;
}): Promise<{ documents: { path: string; page: number; excerpt: string }[] }> {
    const client = createClient();
    const { data } = await client.post('/api/trace/requirement', payload);
    return data;
}

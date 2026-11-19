import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StreamingMarkdown } from '../components/StreamingMarkdown';
import { CodeExecutionBlock } from '../components/CodeExecutionBlock';

describe('Rich Output: StreamingMarkdown and CodeExecutionBlock', () => {
  it('renders localized image tag with data-testid="inline-markdown-image" and strips residual raw googleusercontent placeholders', () => {
    const rawContent = `Here is your generated image:
![Neon Cube](/files/0123456789abcdef0123456789abcdef.png)

http://googleusercontent.com/image_generation_content/0_979

Hope you like it!`;

    const { container } = render(<StreamingMarkdown content={rawContent} />);

    // Check image rendering
    const img = screen.getByTestId('inline-markdown-image') as HTMLImageElement;
    expect(img).toBeDefined();
    expect(img.getAttribute('src')).toBe('/files/0123456789abcdef0123456789abcdef.png');
    expect(img.getAttribute('alt')).toBe('Neon Cube');

    // Check placeholder stripping
    expect(container.textContent).not.toContain('image_generation_content/0_979');
    expect(container.textContent).toContain('Here is your generated image:');
    expect(container.textContent).toContain('Hope you like it!');
  });

  it('renders CodeExecutionBlock with sandbox header, status badges, stdout, and stderr', () => {
    const executions = [
      {
        language: 'python',
        code: 'print("hello world")',
        stdout: 'hello world\n',
        stderr: '',
        outcome: 'OUTCOME_OK',
      },
      {
        language: 'python',
        code: '1 / 0',
        stdout: '',
        stderr: 'ZeroDivisionError: division by zero\n',
        outcome: 'OUTCOME_FAILED',
      },
    ];

    render(<CodeExecutionBlock executions={executions} />);

    // Verify container
    const blocks = screen.getAllByTestId('code-execution-block');
    expect(blocks.length).toBe(1);

    // Verify headers
    expect(screen.getAllByText('Cloud Python Sandbox').length).toBe(2);

    // Verify OK / ERROR statuses
    const statuses = screen.getAllByTestId('code-execution-status');
    expect(statuses.length).toBe(2);
    expect(statuses[0].textContent).toBe('OK');
    expect(statuses[1].textContent).toBe('ERROR');

    // Verify stdout
    const stdoutPre = screen.getByTestId('code-execution-stdout');
    expect(stdoutPre.textContent).toContain('hello world');

    // Verify stderr
    const stderrPre = screen.getByTestId('code-execution-stderr');
    expect(stderrPre.textContent).toContain('ZeroDivisionError: division by zero');
  });

  it('renders nothing when executions array is empty or undefined', () => {
    const { container: c1 } = render(<CodeExecutionBlock executions={[]} />);
    expect(c1.firstChild).toBeNull();

    // @ts-expect-error test falsy prop
    const { container: c2 } = render(<CodeExecutionBlock executions={null} />);
    expect(c2.firstChild).toBeNull();
  });
});
